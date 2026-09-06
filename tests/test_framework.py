import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from agent_reliability.adapters import ArtifactAdapter
from agent_reliability.artifacts import ArtifactStore, assess_artifacts
from agent_reliability.catalog import builtin_suite
from agent_reliability.contracts import Assessment, Case
from agent_reliability.faults import FaultEngine, FaultRule, RetryableFault
from agent_reliability.suite import load_suite, run_matrix

REPO = Path(__file__).resolve().parents[1]


async def test_core_matrix_and_independent_case_state(tmp_path):
    root, rows = await run_matrix(tmp_path, builtin_suite())
    assert len(rows) == 24
    assert all(r["scenario_passed"] for r in rows)
    assert sum(r["task_completed"] for r in rows) == 18
    assert sum(r["observed"] == "violation" for r in rows) == 3
    assert sum(r["observed"] == "rejected" for r in rows) == 3
    for row in rows:
        assert row["usage"] is None and row["cost_usd"] is None
        assert (root / row["id"] / "snapshot.json").is_file()
        assert (root / row["id"] / "faults.sqlite").is_file()
        assert (root / row["id"] / "traces.jsonl").is_file()
    spans = [
        json.loads(s)
        for s in (root / "artifact-lost-ack" / "traces.jsonl").read_text().splitlines()
    ]
    fault = next(s for s in spans if s["name"] == "fault.inject")
    tool = next(s for s in spans if s["name"] == "tool.commit_artifact")
    assert fault["parent_id"] == tool["context"]["span_id"]
    assert "24/24" in (root / "index.html").read_text()
    manifest = json.loads((root / "manifest.json").read_text())
    assert len(manifest["configuration_sha256"]) == 64
    assert set(manifest["adapters"]) == {"refund", "artifact"}


async def test_shared_engine_drives_real_creatorpal_tools(tmp_path, caplog):
    pytest.importorskip("creatorpal_agent")
    cases = [c for c in builtin_suite("full") if c.adapter == "creatorpal"]
    root, rows = await run_matrix(tmp_path, cases)
    assert len(rows) == 8 and all(r["scenario_passed"] for r in rows)
    assert sum(r["task_completed"] for r in rows) == 6
    assert rows[-1]["observed"] == "violation"
    snapshot = json.loads((root / "creatorpal-lost-ack" / "snapshot.json").read_text())
    assert len(snapshot["reports"]) == 1
    assert any(e["kind"] == "retryable_failure" for e in snapshot["events"])
    assert "Failed to detach context" not in caplog.text


def test_schedule_is_atomic_and_survives_new_instances(tmp_path):
    path = tmp_path / "faults.sqlite"
    rule = FaultRule("save", "after", "lost_ack", occurrence=3, repeat=2)
    FaultEngine(path, [rule])

    def invoke(_):
        try:
            return FaultEngine(path, [rule]).apply("save", "after", "ok")
        except RetryableFault:
            return "fault"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(invoke, range(30)))
    assert results.count("fault") == 2
    engine = FaultEngine(path, [rule])
    assert engine.covered()
    assert [e["occurrence"] for e in engine.events()] == [3, 4]
    with pytest.raises(ValueError, match="different schedule"):
        FaultEngine(path, [])


async def test_unreached_fault_cannot_pass(tmp_path):
    case = Case(
        "missed",
        "artifact",
        faults=(FaultRule("commit_artifact", "before", "timeout", occurrence=5),),
    )
    _, rows = await run_matrix(tmp_path, [case])
    assert rows[0]["task_completed"]
    assert not rows[0]["scenario_passed"] and not rows[0]["fault_coverage"]


async def test_partial_schedule_cannot_pass(tmp_path):
    case = Case(
        "partial",
        "artifact",
        faults=(FaultRule("retrieve_records", "before", "timeout", repeat=4),),
        expected="rejected",
    )
    _, rows = await run_matrix(tmp_path, [case])
    assert rows[0]["observed"] == "rejected"
    assert not rows[0]["scenario_passed"]


async def test_unrelated_violation_cannot_replace_expected_negative_control(tmp_path):
    control = next(c for c in builtin_suite() if c.id == "refund-baseline-lost-ack")
    wrong = replace(control, expected_failed_checks=("tenant_scope",))
    _, rows = await run_matrix(tmp_path, [wrong])
    assert rows[0]["observed"] == "violation"
    assert not rows[0]["scenario_passed"]


class BrokenAdapter(ArtifactAdapter):
    async def execute(self, case, directory, faults, tracer):
        raise RuntimeError("sensitive-provider-detail")


async def test_adapter_exception_is_not_a_passing_negative_control(tmp_path):
    root, rows = await run_matrix(
        tmp_path,
        [Case("broken", "broken", expected="violation", expected_failed_checks=("one_artifact",))],
        {"broken": BrokenAdapter()},
    )
    assert not rows[0]["scenario_passed"] and rows[0]["observed"] == "error"
    assert rows[0]["error_type"] == "RuntimeError"
    assert "sensitive-provider-detail" not in (root / "broken" / "traces.jsonl").read_text()
    assert "sensitive-provider-detail" not in (root / "results.json").read_text()


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [Case("duplicate", "artifact")] * 2,
        [Case("unknown", "missing")],
        [Case("invalid", "artifact", config={"unused": 1})],
        [Case("wrong-tool", "artifact", faults=(FaultRule("nonexistent", "before", "timeout"),))],
    ],
)
async def test_invalid_suites_fail_before_execution(tmp_path, cases):
    with pytest.raises(ValueError):
        await run_matrix(tmp_path, cases)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"phase": "unknown"},
        {"kind": "lost_ack"},
        {"repeat": 0},
        {"repeat": True},
        {"occurrence": -1},
        {"tool": ""},
    ],
)
def test_fault_schema_rejects_invalid_values(kwargs):
    values = {"tool": "save", "phase": "before", "kind": "timeout"} | kwargs
    with pytest.raises(ValueError):
        FaultRule(**values)


def test_suite_roundtrip_and_path_traversal_rejection():
    for profile in ("core", "full"):
        assert load_suite(REPO / "examples" / "suites" / f"{profile}.json") == builtin_suite(
            profile
        )
    with pytest.raises(ValueError):
        Case("../escape", "artifact")
    with pytest.raises(ValueError):
        Assessment(True, False, {"ignored_failure": False})
    with pytest.raises(ValueError):
        Assessment(False, False, {"not_a_violation": True})


@pytest.mark.parametrize(
    "tamper", ["file", "aggregate", "receipt", "commit", "checkpoint", "records", "missing"]
)
async def test_artifact_oracle_rejects_tampering(tmp_path, tamper):
    root, rows = await run_matrix(tmp_path, [Case("artifact", "artifact")])
    snapshot = json.loads((root / "artifact" / "snapshot.json").read_text())
    receipt = rows[0]["receipt"].copy()
    if tamper == "file":
        snapshot["files"]["summary.json"] = "corrupted"
    elif tamper == "aggregate":
        snapshot["artifacts"][0]["content"] = '{"total_tasks": 999}'
    elif tamper == "receipt":
        receipt["sha256"] = "forged"
    elif tamper == "commit":
        snapshot["events"].append("artifact_committed")
    elif tamper == "checkpoint":
        snapshot["checkpoint"] = None
    elif tamper == "records":
        snapshot["records"] = []
    else:
        snapshot = {}
    assert not assess_artifacts(snapshot, receipt).completed


@pytest.mark.parametrize(
    "case_id",
    [
        "artifact-restart-before-commit",
        "artifact-restart-after-commit",
        "artifact-restart-after-export",
    ],
)
def test_artifact_new_process_resume(tmp_path, case_id):
    command = [
        sys.executable,
        "-m",
        "agent_reliability",
        "artifact-worker",
        "--directory",
        str(tmp_path),
        "--case-id",
        case_id,
    ]
    first = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert first.returncode == 75, first.stderr
    second = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert second.returncode == 0, second.stderr
    third = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert json.loads(second.stdout) == json.loads(third.stdout)
    snapshot = ArtifactStore(tmp_path).snapshot()
    assert snapshot["events"].count("artifact_committed") == 1
    assert snapshot["events"].count("artifact_exported") == 1
    assert assess_artifacts(snapshot, json.loads(second.stdout)).completed
    wrong = subprocess.run(
        command[:-1] + ["artifact-clean"], text=True, capture_output=True, timeout=15
    )
    assert wrong.returncode != 0 and "different task configuration" in wrong.stderr


def test_sigkill_between_database_commit_and_file_export(tmp_path):
    command = [
        sys.executable,
        "-m",
        "agent_reliability",
        "artifact-worker",
        "--directory",
        str(tmp_path),
        "--case-id",
        "artifact-clean",
    ]
    script = """
import sys, time
from pathlib import Path
from agent_reliability.cli import main
from agent_reliability.faults import FaultEngine
original = FaultEngine.apply
def pause(self, tool, phase, result=None):
    if tool == 'export_artifact' and phase == 'before':
        self.path.with_name('ready').write_text('committed')
        time.sleep(30)
    return original(self, tool, phase, result)
FaultEngine.apply = pause
sys.argv = ['agent-reliability'] + sys.argv[1:]
main()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script] + command[3:], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(child.communicate()[1].decode())
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        assert not (tmp_path / "summary.json").exists()
        child.kill()
        child.communicate(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    resumed = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert resumed.returncode == 0, resumed.stderr
    snapshot = ArtifactStore(tmp_path).snapshot()
    assert snapshot["events"].count("artifact_committed") == 1
    assert assess_artifacts(snapshot, json.loads(resumed.stdout)).completed


def test_external_plugin_cli_and_nonzero_exit_on_unexpected_outcome(tmp_path):
    command = [
        sys.executable,
        "-m",
        "agent_reliability",
        "run",
        "--plugin",
        "kv=custom_adapter:create_adapter",
        "--suite",
        str(REPO / "examples/suites/custom.json"),
        "--output",
        str(tmp_path),
    ]
    environment = os.environ | {"PYTHONPATH": str(REPO / "examples")}
    result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "1/1" in result.stdout
    wrong = replace(load_suite(REPO / "examples/suites/custom.json")[0], expected="rejected")
    bad_suite = tmp_path / "wrong.json"
    bad_suite.write_text(json.dumps({"schema_version": 1, "cases": [wrong.to_dict()]}))
    command[command.index("--suite") + 1] = str(bad_suite)
    result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=15)
    assert result.returncode == 1 and "0/1" in result.stdout
