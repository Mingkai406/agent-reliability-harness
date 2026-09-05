"""External CreatorPal faults and an independent, versioned snapshot grader.

This module deliberately does not import CreatorPal's completion/quality grader.
The optional application package is loaded only when its suite is selected.
"""

import hashlib
import json
import uuid
from pathlib import Path

SCENARIOS = (
    "clean",
    "retrieval_timeout",
    "malformed_retrieval",
    "analytics_timeout",
    "interrupted_before_publish",
    "lost_report_ack",
    "interrupted_after_publish",
    "false_completion",
)
EXPECTED_FAILURES = {"analytics_timeout", "false_completion"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def grade_snapshot(snapshot, receipt):
    """Check committed artifacts, references and receipt, without trusting app pass flags.

    This is structural consistency, not a semantic-grounding judge or a hostile-app attestation.
    """
    checks = {}
    try:
        checks["contract"] = snapshot["contract_version"] == 1
        task = snapshot["task"]
        reports = snapshot["reports"]
        checks["one_report"] = len(reports) == 1
        if not checks["one_report"]:
            return {"completed": False, "checks": checks}
        report = reports[0]
        evidence, analyses = snapshot["evidence"], snapshot["analyses"]
        checks["task_scope"] = report["task_id"] == task["id"]
        recs = report["recommendations"]
        checks["recommendation_count"] = 1 <= len(recs) <= task["max_results"]
        checks["unique_communities"] = len({r["community"] for r in recs}) == len(recs)
        checks["citations"] = all(
            bool(rec["evidence_ids"])
            and all(
                key in evidence
                and evidence[key]["id"] == key
                and evidence[key]["community"] == rec["community"]
                for key in rec["evidence_ids"]
            )
            for rec in recs
        )
        checks["required_sources"] = checks["citations"] and all(
            {"profile", "rules"}.issubset({evidence[k]["kind"] for k in rec["evidence_ids"]})
            if task["needs_rules"]
            else "profile" in {evidence[k]["kind"] for k in rec["evidence_ids"]}
            for rec in recs
        )
        analysis_id = report.get("analysis_id")
        checks["analysis"] = (not task["needs_analytics"] and not analysis_id) or (
            bool(analysis_id)
            and analysis_id in analyses
            and "result" in analyses[analysis_id]
            and all(row["evidence_id"] in evidence for row in analyses[analysis_id]["rows"])
        )
        checks["single_commit"] = (
            sum(event["kind"] == "report_committed" for event in snapshot["events"]) == 1
        )
        checks["receipt"] = (
            isinstance(receipt, dict)
            and receipt.get("status") == "complete"
            and (
                receipt.get("task_id") == task["id"]
                and receipt.get("report_sha256") == digest(report)
            )
        )
    except (KeyError, TypeError, ValueError):
        checks["schema"] = False
    return {"completed": bool(checks) and all(checks.values()), "checks": checks}


def fault_hook(scenario):
    from creatorpal_agent.runtime import (
        InterruptedTask,
        RetryableToolError,
        ToolExecutionError,
        ToolHook,
    )

    class Inject(ToolHook):
        def trigger(self, state):
            if state.once("harness-fault:" + scenario):
                state.event("harness_fault", scenario=scenario)
                return True
            return False

        def before(self, name, arguments, state):
            if name == "search_communities" and scenario == "retrieval_timeout":
                if self.trigger(state):
                    raise RetryableToolError("InjectedTimeout")
            if name == "run_analysis" and scenario == "analytics_timeout":
                # Every attempt fails; a success must not be fabricated after an execution timeout.
                self.trigger(state)
                raise ToolExecutionError("Timeout")
            if name == "publish_report" and scenario == "interrupted_before_publish":
                if self.trigger(state):
                    raise InterruptedTask("BeforeCommit")

        def after(self, name, arguments, result, state):
            if name == "search_communities" and scenario == "malformed_retrieval":
                if self.trigger(state):
                    return {"status": "ok", "evidence": [{"id": "broken"}]}
            if name == "publish_report" and scenario in {
                "lost_report_ack",
                "interrupted_after_publish",
            }:
                if self.trigger(state):
                    if scenario == "lost_report_ack":
                        raise RetryableToolError("LostAcknowledgement")
                    raise InterruptedTask("AfterCommit")
            return result

    return Inject()


def false_completion_model():
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class FalseCompletion(BaseLlm):
        model: str = "offline-false-completion-double"

        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=json.dumps(
                                {
                                    "status": "complete",
                                    "task_id": "harness-audience",
                                    "report_sha256": "invented-without-publish",
                                }
                            )
                        )
                    ],
                )
            )

    return FalseCompletion()


async def run_creatorpal_suite(output, *, adapter="offline-adk"):
    if adapter not in {"scripted", "offline-adk"}:
        raise ValueError(
            "This fault suite uses offline controls; live model experiments are separate"
        )
    from creatorpal_agent.contracts import ResearchTask
    from creatorpal_agent.experiment import run_experiment

    root = Path(output) / ("creatorpal-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True)
    task = ResearchTask(
        id="harness-audience",
        query="Compare Python data analysis tutorials using engagement_rate and community rules.",
        needs_rules=True,
        needs_analytics=True,
        max_results=2,
    )
    results = []
    for scenario in SCENARIOS:
        directory = root / scenario
        # The false-completion case always crosses the ADK model boundary.
        actual_adapter = "offline-adk" if scenario == "false_completion" else adapter
        result = await run_experiment(
            task,
            directory,
            adapter=actual_adapter,
            hook=fault_hook(scenario),
            offline_model_factory=(
                false_completion_model if scenario == "false_completion" else None
            ),
        )
        snapshot = json.loads((directory / "state-snapshot.json").read_text())
        grade = grade_snapshot(snapshot, result["receipt"])
        injected = scenario in {"clean", "false_completion"} or any(
            e["kind"] == "harness_fault" and e["scenario"] == scenario for e in snapshot["events"]
        )
        if scenario in EXPECTED_FAILURES:
            expected_error = (
                result["receipt"].get("status") != "complete"
                and any(
                    e["kind"] == "execution_failure" and e.get("error_type") == "Timeout"
                    for e in snapshot["events"]
                )
                if scenario == "analytics_timeout"
                else result["receipt"].get("status") == "complete"
            )
            passed = (
                injected and expected_error and not grade["completed"] and not snapshot["reports"]
            )
            expected = "reject_without_report"
        else:
            passed = injected and grade["completed"]
            expected = "complete_once"
        results.append(
            {
                "scenario": scenario,
                "adapter": actual_adapter,
                "expected": expected,
                "fault_observed": injected,
                "scenario_passed": passed,
                "task_completed": grade["completed"],
                "checks": grade["checks"],
                "receipt": result["receipt"],
            }
        )
    manifests = {
        name: json.loads((root / name / "manifest.json").read_text()) for name in SCENARIOS
    }
    manifest = {
        "suite": "creatorpal",
        "snapshot_contract": 1,
        "scenario_order": list(SCENARIOS),
        "task": task.model_dump(),
        "application_configurations": manifests,
        "measurement": "Synthetic faults and offline model controls; not measured LLM reliability",
        "grader_boundary": "Independent artifact checks; not a hostile-process security boundary",
    }
    (root / "suite-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "suite-results.json").write_text(json.dumps(results, indent=2) + "\n")
    rows = [
        "# CreatorPal reliability experiments",
        "",
        "Synthetic controls through real application tools. Expected safe rejection counts as a "
        "scenario pass, not a completed research task. No live LLM capability is measured.",
        "",
        "| Scenario | Adapter | Expected | Scenario passed | Task completed |",
        "|---|---|---|---|---|",
    ]
    for row in results:
        rows.append(
            f"| {row['scenario']} | {row['adapter']} | {row['expected']} | "
            f"{row['scenario_passed']} | {row['task_completed']} |"
        )
    (root / "report.md").write_text("\n".join(rows) + "\n")
    return root, results
