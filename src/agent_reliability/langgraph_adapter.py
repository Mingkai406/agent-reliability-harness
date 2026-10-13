"""Real LangGraph execution with disk checkpoints and an independent business-state oracle.

Nodes are deterministic controls, not live model calls. Checkpoints and business commits
deliberately use separate databases: recovery must tolerate replay of a committed side effect.
"""

import asyncio
import hashlib
import json
from collections import Counter
from typing import TypedDict

from .artifacts import RECORDS, ArtifactStore, assess_artifacts
from .contracts import Assessment, Execution
from .faults import InterruptedFault, PermanentFault, RetryableFault


class WorkflowState(TypedDict, total=False):
    records: list[dict] | None
    content: str
    receipt: dict


def build_graph(
    store, faults, tracer, checkpointer, *, invalid_receipt=False, state_layout="inline"
):
    if state_layout not in {"inline", "compact"}:
        raise ValueError("Unknown state layout")
    attempts = Counter()
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import RetryPolicy

    def tool(name, action, validate):
        # Retries belong to LangGraph's node policy, not to a hidden outer tool loop.
        store.event("node_attempt:" + name)
        attempts[name] += 1
        with tracer.start_as_current_span(
            "tool." + name,
            attributes={
                "tool.name": name,
                "tool.attempt": attempts[name],
                "state.layout": state_layout,
            },
        ):
            faults.apply(name, "before")
            result = faults.apply(name, "after", action())
            if not validate(result):
                raise RetryableFault("invalid_response")
            return result

    def retrieve(state):
        records = tool("retrieve_records", lambda: RECORDS, lambda r: r == RECORDS)
        store.put("records", records)
        return {"records": records if state_layout == "inline" else None}

    def build(state):
        records = state["records"] if state_layout == "inline" else store.get("records")
        if records != RECORDS:
            raise ValueError("Persisted source records changed or disappeared")
        content = tool(
            "build_artifact",
            lambda: json.dumps({"total_tasks": sum(r["tasks"] for r in records)}, sort_keys=True),
            lambda r: isinstance(r, str) and r == '{"total_tasks": 20}',
        )
        return {"content": content}

    def commit(state):
        digest = hashlib.sha256(state["content"].encode()).hexdigest()
        tool(
            "commit_artifact",
            lambda: store.commit(state["content"]),
            lambda r: r == {"status": "committed", "sha256": digest},
        )
        return {}

    def export(state):
        digest = hashlib.sha256(state["content"].encode()).hexdigest()
        receipt = tool(
            "export_artifact", store.export, lambda r: r == {"status": "complete", "sha256": digest}
        )
        return {"receipt": receipt}

    def finish(state):
        with tracer.start_as_current_span("state.finish"):
            store.put("complete", state["receipt"])
        # Deliberately broken control: the graph completes with an ungrounded receipt.
        return {
            "receipt": {"status": "complete", "sha256": "incorrect"}
            if invalid_receipt
            else state["receipt"]
        }

    graph = StateGraph(WorkflowState)
    retry = RetryPolicy(
        max_attempts=3, initial_interval=0.001, jitter=False, retry_on=RetryableFault
    )
    nodes = [
        ("retrieve", retrieve),
        ("build", build),
        ("commit", commit),
        ("export", export),
        ("finish", finish),
    ]
    previous = START
    for name, node in nodes:
        graph.add_node(name, node, retry_policy=retry)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile(checkpointer=checkpointer)


def _run_graph_once(case, directory, faults, tracer):
    """One invocation; a fresh process can resume it using the same directory and case."""
    from .state_telemetry import TracedSqliteSaver

    store = ArtifactStore(directory)
    store.bind({"adapter_version": 1, "case": case.to_dict()})
    config = {"configurable": {"thread_id": case.id}}
    with TracedSqliteSaver.from_conn_string(str(directory / "checkpoints.sqlite")) as saver:
        saver.tracer = tracer
        graph = build_graph(
            store,
            faults,
            tracer,
            saver,
            invalid_receipt=case.config.get("mode") == "invalid_receipt",
            state_layout=case.config.get("state_layout", "inline"),
        )
        prior = graph.get_state(config)
        from opentelemetry import trace

        span = trace.get_current_span()
        span.set_attribute("workflow.resumed", bool(prior.values or prior.next))
        span.set_attribute("workflow.pending_nodes", list(prior.next))
        receipt = store.get("rejected")
        if receipt is None:
            try:
                if prior.values and not prior.next:
                    values = prior.values
                else:
                    values = graph.invoke(
                        None if prior.values or prior.next else {}, config, durability="sync"
                    )
                receipt = values["receipt"]
            except (PermanentFault, RetryableFault):
                store.event("execution_rejected")
                receipt = {"status": "failed"}
                store.put("rejected", receipt)
            except InterruptedFault:
                store.event("worker_interrupted")
                raise
        checkpoint = graph.get_state(config)
        snapshot = store.snapshot()
        snapshot["graph"] = {
            "checkpoint_id": checkpoint.config.get("configurable", {}).get("checkpoint_id"),
            "next": list(checkpoint.next),
            "values": checkpoint.values,
        }
    return Execution(receipt, snapshot, "LangGraph + deterministic nodes; no model calls")


def run_graph_once(case, directory, faults, tracer):
    """Trace each invocation and link a resumed process to its prior invocation."""
    from .state_telemetry import invocation_span

    with invocation_span(directory, tracer, case.id, case.config.get("state_layout", "inline")):
        return _run_graph_once(case, directory, faults, tracer)


class LangGraphAdapter:
    def validate(self, case):
        # Import here keeps the core installation free of LangGraph dependencies.
        from .adapters import validate_options

        validate_options(
            case,
            {"mode": ("normal", "invalid_receipt"), "state_layout": ("inline", "compact")},
            {"retrieve_records", "build_artifact", "commit_artifact", "export_artifact"},
        )
        try:
            import langgraph.checkpoint.sqlite  # noqa: F401
            import langgraph.graph  # noqa: F401
        except ImportError as exc:
            raise ValueError("Install the LangGraph extra: pip install -e '.[langgraph]'") from exc

    async def execute(self, case, directory, faults, tracer):
        for attempt in range(2):
            try:
                return await asyncio.to_thread(run_graph_once, case, directory, faults, tracer)
            except InterruptedFault:
                if attempt == 1:
                    raise

    def assess(self, case, execution):
        assessment = assess_artifacts(execution.snapshot, execution.receipt)
        graph = execution.snapshot.get("graph", {})
        checks = dict(assessment.checks)
        checks["persisted_graph_checkpoint"] = bool(graph.get("checkpoint_id"))
        if execution.receipt.get("status") == "complete":
            checks["graph_finished"] = graph.get("next") == []
            checks["graph_receipt_matches"] = (
                graph.get("values", {}).get("receipt") == execution.receipt
            )
        return Assessment(
            assessment.completed and all(checks.values()),
            assessment.safely_rejected and all(checks.values()),
            checks,
        )
