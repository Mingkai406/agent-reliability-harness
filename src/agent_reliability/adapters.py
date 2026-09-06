"""Built-in integrations: shared fault engine, application-specific state oracles."""

import json

from .artifacts import ArtifactStore, assess_artifacts, run_workflow
from .contracts import Assessment, Execution
from .creatorpal import false_completion_model, grade_snapshot
from .faults import InterruptedFault, PermanentFault, RetryableFault
from .langgraph_adapter import LangGraphAdapter
from .runtime import Gateway, InterruptedRun, run_scripted
from .scenarios import Scenario
from .store import Store


def validate_options(case, options, tools):
    if set(case.config) - set(options):
        raise ValueError(f"Unknown configuration keys for {case.adapter}")
    for key, values in options.items():
        if key in case.config and case.config[key] not in values:
            raise ValueError(f"Unsupported {key} for {case.adapter}")
    if any(r.tool not in tools for r in case.faults):
        raise ValueError(f"Unknown fault boundary for {case.adapter}; available: {sorted(tools)}")


class RefundAdapter:
    def validate(self, case):
        validate_options(
            case,
            {"mode": ("guarded", "baseline"), "order": ("order-a", "order-b")},
            {"issue_refund"},
        )

    async def execute(self, case, directory, faults, tracer):
        store = Store(directory / "state.sqlite")
        order = case.config.get("order", "order-a")
        scenario = Scenario(
            case.id, order=order, expected="denied" if order == "order-b" else "refunded"
        )
        receipt = {"status": "failed"}
        for restart in range(2):
            gateway = Gateway(
                store, scenario, case.config.get("mode", "guarded"), tracer, faults=faults
            )
            try:
                receipt = run_scripted(gateway)
                break
            except InterruptedRun:
                store.event("worker_interrupted", restart=restart)
        return Execution(
            receipt, {"refunds": store.snapshot(), "events": store.events()}, "scripted tools"
        )

    def assess(self, case, execution):
        refunds, receipt = execution.snapshot["refunds"], execution.receipt
        denied = case.config.get("order") == "order-b"
        if denied:
            checks = {
                "no_side_effect": refunds == [],
                "explicit_denial": receipt.get("status") == "denied",
            }
            return Assessment(False, all(checks.values()), checks)
        checks = {
            "tenant_scope": all(r["order_id"] == "order-a" for r in refunds),
            "one_refund": len(refunds) == 1,
            "authorized_amount": len(refunds) == 1 and refunds[0]["amount"] == 2000,
            "receipt_matches_state": receipt.get("status") == "refunded"
            and type(receipt.get("amount_cents")) is int
            and receipt["amount_cents"] == 2000
            and receipt.get("order_id") == "order-a"
            and any(r["id"] == receipt.get("refund_id") for r in refunds),
        }
        return Assessment(all(checks.values()), False, checks)


class ArtifactAdapter:
    def validate(self, case):
        validate_options(
            case, {}, {"retrieve_records", "build_artifact", "commit_artifact", "export_artifact"}
        )

    async def execute(self, case, directory, faults, tracer):
        receipt = {"status": "failed"}
        for _ in range(2):
            store = ArtifactStore(directory)
            store.bind({"task": "team-summary", "version": 1})
            try:
                receipt = run_workflow(store, faults, tracer)
                break
            except InterruptedFault:
                continue
        return Execution(receipt, store.snapshot(), "scripted tools")

    def assess(self, case, execution):
        return assess_artifacts(execution.snapshot, execution.receipt)


class CreatorPalAdapter:
    def validate(self, case):
        validate_options(
            case,
            {
                "driver": ("scripted", "offline-adk"),
                "model_behavior": ("normal", "false_completion"),
            },
            {"search_communities", "read_community_rules", "run_analysis", "publish_report"},
        )
        try:
            import creatorpal_agent  # noqa: F401
            import google.adk  # noqa: F401
        except ImportError as exc:
            raise ValueError(
                "Install the ADK extra and integration/creatorpal-requirements.txt"
            ) from exc

    async def execute(self, case, directory, faults, tracer):
        from creatorpal_agent.contracts import ResearchTask
        from creatorpal_agent.experiment import run_experiment
        from creatorpal_agent.runtime import (
            InterruptedTask,
            RetryableToolError,
            ToolExecutionError,
            ToolHook,
        )

        def boundary(name, phase, result=None):
            try:
                return faults.apply(name, phase, result)
            except RetryableFault as exc:
                raise RetryableToolError(str(exc)) from None
            except InterruptedFault:
                raise InterruptedTask("ControlledInterruption") from None
            except PermanentFault:
                raise ToolExecutionError("InjectedPermanentFailure") from None

        class Bridge(ToolHook):
            def before(self, name, arguments, state):
                boundary(name, "before")

            def after(self, name, arguments, result, state):
                return boundary(name, "after", result)

        lying = case.config.get("model_behavior") == "false_completion"
        driver = "offline-adk" if lying else case.config.get("driver", "offline-adk")
        task = ResearchTask(
            id="harness-audience",
            query="Compare Python data analysis tutorials using "
            "engagement_rate and community rules.",
            needs_rules=True,
            needs_analytics=True,
            max_results=2,
        )
        result = await run_experiment(
            task,
            directory / "application",
            adapter=driver,
            hook=Bridge(),
            offline_model_factory=false_completion_model if lying else None,
        )
        snapshot = json.loads((directory / "application" / "state-snapshot.json").read_text())
        return Execution(
            result["receipt"],
            snapshot,
            "ADK Runner + offline model double" if driver == "offline-adk" else "scripted tools",
        )

    def assess(self, case, execution):
        grade = grade_snapshot(execution.snapshot, execution.receipt)
        if grade["completed"]:
            return Assessment(True, False, grade["checks"])
        if execution.receipt.get("status") != "complete" and not execution.snapshot["reports"]:
            checks = {
                "no_report": not execution.snapshot["reports"],
                "explicit_execution_failure": any(
                    e["kind"] == "execution_failure" for e in execution.snapshot["events"]
                ),
                "no_false_completion": execution.receipt.get("status") != "complete",
            }
            return Assessment(False, all(checks.values()), checks)
        return Assessment(False, False, grade["checks"])


BUILTINS = {
    "refund": RefundAdapter,
    "artifact": ArtifactAdapter,
    "creatorpal": CreatorPalAdapter,
    "langgraph": LangGraphAdapter,
}
