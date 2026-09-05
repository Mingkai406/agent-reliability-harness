"""Exercise the actual ADK Runner with an offline model double, not Gemini inference."""

import json
import re

import pytest

pytest.importorskip("google.adk")
from google.adk.agents.invocation_context import LlmCallsLimitExceededError  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

from agent_reliability.adk_adapter import run_adk  # noqa: E402
from agent_reliability.experiment import grade  # noqa: E402
from agent_reliability.runtime import Gateway, InterruptedRun  # noqa: E402
from agent_reliability.scenarios import SCENARIOS  # noqa: E402
from agent_reliability.store import Store  # noqa: E402
from agent_reliability.telemetry import provider_for  # noqa: E402


class OfflineToolModel(BaseLlm):
    model: str = "offline-tool-double"

    async def generate_content_async(self, llm_request, stream=False):
        parts = [part for content in llm_request.contents for part in content.parts or []]
        responses = [p.function_response for p in parts if p.function_response]
        prompt = " ".join(p.text for p in parts if p.text)
        order = re.search(r"order-[ab]", prompt).group()
        if not responses:
            part = types.Part(
                function_call=types.FunctionCall(name="lookup_order", args={"order_id": order})
            )
        elif responses[-1].name == "lookup_order" and (
            responses[-1].response.get("status") != "denied"
        ):
            part = types.Part(
                function_call=types.FunctionCall(
                    name="issue_refund", args={"order_id": order, "amount_cents": 2000}
                )
            )
        else:
            part = types.Part(text=json.dumps(responses[-1].response))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
@pytest.mark.parametrize("mode", ["baseline", "guarded"])
async def test_adk_tool_loop_and_durable_recovery(tmp_path, scenario, mode):
    store = Store(tmp_path / "state.sqlite")
    provider = provider_for(tmp_path / "trace.jsonl")
    response = {}
    try:
        for _ in range(2):
            gateway = Gateway(store, scenario, mode, provider.get_tracer("test"))
            try:
                response, usage = await run_adk(gateway, OfflineToolModel())
                assert usage is None  # Offline double must never imply measured model usage.
                break
            except InterruptedRun:
                continue
        baseline_failures = {"after_commit_timeout", "malformed_response", "interrupted_run"}
        assert grade(scenario, store, response)["passed"] == (
            mode == "guarded" or scenario.fault not in baseline_failures
        ), response
    finally:
        provider.shutdown()


class LoopingModel(BaseLlm):
    model: str = "offline-looping-double"

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="issue_refund", args={"order_id": "order-a", "amount_cents": 2000}
                        )
                    )
                ],
            )
        )


async def test_adk_loop_budget_stops_agent_without_duplicate_refunds(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    provider = provider_for(tmp_path / "trace.jsonl")
    gateway = Gateway(store, SCENARIOS[0], "guarded", provider.get_tracer("test"))
    try:
        with pytest.raises(LlmCallsLimitExceededError):
            await run_adk(gateway, LoopingModel())
        attempts = sum(e["kind"] == "tool_attempt" for e in store.events())
        assert 1 < attempts <= 8
        assert len(store.snapshot()) == 1
        assert store.get("checkpoint") is None
    finally:
        provider.shutdown()


class FailingProviderModel(BaseLlm):
    model: str = "offline-error-double"

    async def generate_content_async(self, llm_request, stream=False):
        raise RuntimeError("synthetic-sensitive-provider-detail")
        yield  # Keep the provider interface an async generator.


async def test_provider_error_details_are_not_written_to_traces(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    trace = tmp_path / "trace.jsonl"
    provider = provider_for(trace)
    gateway = Gateway(store, SCENARIOS[0], "guarded", provider.get_tracer("test"))
    try:
        with pytest.raises(RuntimeError):
            await run_adk(gateway, FailingProviderModel())
    finally:
        provider.shutdown()
    assert "synthetic-sensitive-provider-detail" not in trace.read_text()


class LyingModel(BaseLlm):
    model: str = "offline-lying-double"

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=json.dumps(
                            {
                                "status": "refunded",
                                "refund_id": "fiction",
                                "order_id": "order-a",
                                "amount_cents": 2000,
                            }
                        )
                    )
                ],
            )
        )


async def test_adk_false_completion_is_not_checkpointed(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    provider = provider_for(tmp_path / "trace.jsonl")
    gateway = Gateway(store, SCENARIOS[0], "guarded", provider.get_tracer("test"))
    try:
        response, _ = await run_adk(gateway, LyingModel())
        assert not grade(SCENARIOS[0], store, response)["passed"]
        assert store.get("checkpoint") is None
    finally:
        provider.shutdown()
