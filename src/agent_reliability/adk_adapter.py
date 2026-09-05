"""Real ADK Runner integration; live inference is an explicit, separately measured command."""

import asyncio
import json
import uuid


async def run_adk(gateway, model, *, timeout_seconds=60):
    from google.adk.agents import LlmAgent
    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    if model is None:
        raise ValueError("An explicit model is required")
    checkpoint = gateway.store.get("checkpoint", {}) if gateway.mode == "guarded" else {}
    if checkpoint.get("complete"):
        return checkpoint["result"], None

    # Capture injected worker interruptions outside ADK's tool-error handling as well.
    from .runtime import InterruptedRun

    interrupted = False

    def lookup_order(order_id: str) -> dict:
        """Look up an order belonging to the authenticated user."""
        return gateway.lookup_order(order_id)

    def issue_refund(order_id: str, amount_cents: int) -> dict:
        """Issue the requested refund in cents and return its receipt, or a denial."""
        nonlocal interrupted
        try:
            return gateway.issue_refund(order_id, amount_cents)
        except InterruptedRun:
            interrupted = True
            raise

    agent = LlmAgent(
        name="refund_agent",
        model=model,
        instruction=(
            "You handle one authorized refund request. First look up the order. "
            "If denied, stop. Otherwise call issue_refund once with the requested amount. "
            "The tool layer handles retries. Do not invent receipts or change the request. "
            "Return the final tool result verbatim as one JSON object, without markdown."
        ),
        tools=[lookup_order, issue_refund],
        generate_content_config=types.GenerateContentConfig(temperature=0),
    )
    sessions = InMemorySessionService()
    session_id = uuid.uuid4().hex
    await sessions.create_session(
        app_name="harness", user_id="synthetic-user", session_id=session_id
    )
    runner = Runner(agent=agent, app_name="harness", session_service=sessions)
    response = {"status": "failed", "error": "Agent returned no valid JSON completion"}
    usage = {"input_tokens": 0, "output_tokens": 0, "model_responses": 0}
    usage_reported = False

    async def invoke():
        nonlocal response, usage_reported
        message = types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        f"Refund {gateway.scenario.amount_cents} cents "
                        f"for {gateway.scenario.order}."
                    )
                )
            ],
        )
        with gateway.tracer.start_as_current_span("agent.adk.run"):
            async for event in runner.run_async(
                user_id="synthetic-user",
                session_id=session_id,
                new_message=message,
                run_config=RunConfig(max_llm_calls=8),
            ):
                if interrupted:
                    raise InterruptedRun("Injected worker interruption")
                metadata = event.usage_metadata
                if metadata:
                    usage_reported = True
                    usage["input_tokens"] += metadata.prompt_token_count or 0
                    usage["output_tokens"] += metadata.candidates_token_count or 0
                    usage["model_responses"] += 1
                if event.is_final_response() and event.content:
                    raw = "".join(p.text or "" for p in event.content.parts or [])
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            response = parsed
                    except json.JSONDecodeError:
                        pass

    try:
        await asyncio.wait_for(invoke(), timeout=timeout_seconds)
    finally:
        await runner.close()
    if gateway.mode == "guarded" and response.get("status") in {"refunded", "denied"}:
        # Only checkpoint a completion whose receipt is verified against durable state.
        from .experiment import grade

        if grade(gateway.scenario, gateway.store, response)["passed"]:
            gateway.store.set("checkpoint", {"complete": True, "result": response})
    return response, usage if usage_reported else None
