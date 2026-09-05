import copy
import json

import pytest

pytest.importorskip("creatorpal_agent")
from agent_reliability.creatorpal import (  # noqa: E402
    SCENARIOS,
    digest,
    grade_snapshot,
    run_creatorpal_suite,
)


@pytest.fixture
async def suite(tmp_path):
    root, results = await run_creatorpal_suite(tmp_path)
    return root, results


async def test_all_faults_have_expected_state_outcomes(suite, caplog):
    root, results = suite
    assert len(results) == len(SCENARIOS) == 8
    assert all(row["scenario_passed"] for row in results)
    assert sum(row["task_completed"] for row in results) == 6
    assert {row["scenario"] for row in results if not row["task_completed"]} == {
        "analytics_timeout",
        "false_completion",
    }
    for scenario in ("retrieval_timeout", "malformed_retrieval", "lost_report_ack"):
        snapshot = json.loads((root / scenario / "state-snapshot.json").read_text())
        assert any(e["kind"] == "retryable_failure" for e in snapshot["events"])
        assert len(snapshot["reports"]) == 1
    for scenario in ("interrupted_before_publish", "interrupted_after_publish"):
        snapshot = json.loads((root / scenario / "state-snapshot.json").read_text())
        assert any(e["kind"] == "task_interrupted" for e in snapshot["events"])
    assert "Failed to detach context" not in caplog.text


async def test_scripted_control_crosses_adk_for_lying_model(tmp_path):
    _, results = await run_creatorpal_suite(tmp_path, adapter="scripted")
    assert all(row["scenario_passed"] for row in results)
    lying = next(row for row in results if row["scenario"] == "false_completion")
    assert lying["adapter"] == "offline-adk"
    assert not lying["task_completed"]


@pytest.mark.parametrize(
    "tamper",
    [
        "no-report",
        "duplicate-report",
        "false-receipt",
        "wrong-task",
        "unknown-citation",
        "cross-community",
        "missing-rules",
        "missing-analysis",
        "duplicate-commit",
        "wrong-contract",
    ],
)
async def test_independent_grader_rejects_corrupted_artifacts(tmp_path, tamper):
    from creatorpal_agent.contracts import ResearchTask
    from creatorpal_agent.experiment import run_experiment

    result = await run_experiment(
        ResearchTask(
            id="negative",
            query="Python data analysis tutorials",
            needs_rules=True,
            needs_analytics=True,
            max_results=2,
        ),
        tmp_path,
    )
    snapshot = json.loads((tmp_path / "state-snapshot.json").read_text())
    receipt = copy.deepcopy(result["receipt"])
    assert grade_snapshot(snapshot, receipt)["completed"]
    if tamper == "no-report":
        snapshot["reports"] = []
    elif tamper == "duplicate-report":
        snapshot["reports"] *= 2
    elif tamper == "false-receipt":
        receipt["report_sha256"] = "false"
    elif tamper == "wrong-task":
        snapshot["reports"][0]["task_id"] = "another"
    elif tamper == "unknown-citation":
        snapshot["reports"][0]["recommendations"][0]["evidence_ids"] = ["fake"]
    elif tamper == "cross-community":
        snapshot["reports"][0]["recommendations"][0]["community"] = "another"
    elif tamper == "missing-rules":
        for rec in snapshot["reports"][0]["recommendations"]:
            rec["evidence_ids"] = [
                k for k in rec["evidence_ids"] if snapshot["evidence"][k]["kind"] != "rules"
            ]
    elif tamper == "missing-analysis":
        snapshot["analyses"] = {}
    elif tamper == "duplicate-commit":
        snapshot["events"].append({"kind": "report_committed"})
    else:
        snapshot["contract_version"] = 99
    if tamper != "false-receipt" and len(snapshot["reports"]) == 1:
        receipt["report_sha256"] = digest(snapshot["reports"][0])
    assert not grade_snapshot(snapshot, receipt)["completed"]


@pytest.mark.parametrize("snapshot", [{}, {"contract_version": 1, "task": {}, "reports": None}])
def test_grader_fails_closed_on_malformed_snapshot(snapshot):
    assert not grade_snapshot(snapshot, {})["completed"]
