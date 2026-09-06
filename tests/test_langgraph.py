"""Exercise the actual LangGraph runtime, disk checkpoints and independent state checks."""

import copy
import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

pytest.importorskip("langgraph.graph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from agent_reliability.artifacts import ArtifactStore
from agent_reliability.catalog import langgraph_suite
from agent_reliability.contracts import Case, Execution
from agent_reliability.faults import FaultRule
from agent_reliability.langgraph_adapter import LangGraphAdapter
from agent_reliability.suite import run_matrix


async def test_langgraph_matrix_real_checkpoints_and_traces(tmp_path):
    root, rows = await run_matrix(tmp_path, langgraph_suite())
    assert len(rows) == 10 and all(r["scenario_passed"] for r in rows)
    assert sum(r["task_completed"] for r in rows) == 7
    assert sum(r["observed"] == "rejected" for r in rows) == 2
    assert rows[-1]["observed"] == "violation"
    assert set(rows[-1]["expected_failed_checks"]) == {"matching_receipt", "completion_checkpoint"}
    for row in rows:
        assert row["usage"] is None and row["cost_usd"] is None
        assert (root / row["id"] / "checkpoints.sqlite").is_file()
        assert row["checks"]["persisted_graph_checkpoint"]
    snapshot = json.loads((root / "langgraph-restart-after-commit" / "snapshot.json").read_text())
    assert snapshot["events"].count("node_attempt:retrieve_records") == 1
    assert snapshot["events"].count("node_attempt:commit_artifact") == 2
    assert snapshot["events"].count("artifact_committed") == 1
    assert snapshot["graph"]["next"] == []
    spans = [
        json.loads(s)
        for s in (root / "langgraph-lost-ack" / "traces.jsonl").read_text().splitlines()
    ]
    fault = next(s for s in spans if s["name"] == "fault.inject")
    assert any(
        s["name"] == "tool.commit_artifact" and s["context"]["span_id"] == fault["parent_id"]
        for s in spans
    )
    assert "LangGraph workflow" in (root / "index.html").read_text()
    manifest = json.loads((root / "manifest.json").read_text())
    assert "langgraph" in manifest["packages"]
    assert manifest["measurement"] == "Deterministic offline controls; no live model inference"


@pytest.mark.parametrize(
    "name", ["restart-before-commit", "restart-after-commit", "restart-after-export"]
)
def test_new_process_resumes_and_completed_replay_is_noop(tmp_path, name):
    command = [
        sys.executable,
        "-m",
        "agent_reliability",
        "langgraph-worker",
        "--directory",
        str(tmp_path),
        "--case-id",
        "langgraph-" + name,
    ]
    first = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert first.returncode == 75, first.stderr
    second = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert second.returncode == 0, second.stderr
    snapshot = ArtifactStore(tmp_path).snapshot()
    third = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert third.returncode == 0 and json.loads(second.stdout) == json.loads(third.stdout)
    assert ArtifactStore(tmp_path).snapshot() == snapshot
    assert snapshot["events"].count("node_attempt:retrieve_records") == 1
    assert snapshot["events"].count("artifact_committed") == 1
    wrong = subprocess.run(
        command[:-1] + ["langgraph-clean"], capture_output=True, text=True, timeout=20
    )
    assert wrong.returncode != 0 and "different" in wrong.stderr


def test_sigkill_after_commit_before_graph_checkpoint(tmp_path):
    # Stop inside the node AFTER its real DB commit but BEFORE it returns to LangGraph.
    script = """
import sys, time
from agent_reliability.cli import main
from agent_reliability.faults import FaultEngine
original = FaultEngine.apply
def pause(self, tool, phase, result=None):
    if tool == "commit_artifact" and phase == "after":
        self.path.with_name("ready").write_text("committed")
        time.sleep(30)
    return original(self, tool, phase, result)
FaultEngine.apply = pause
sys.argv = ["agent-reliability"] + sys.argv[1:]
main()
"""
    args = ["langgraph-worker", "--directory", str(tmp_path), "--case-id", "langgraph-clean"]
    child = subprocess.Popen(
        [sys.executable, "-c", script] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(child.communicate()[1].decode())
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        before = ArtifactStore(tmp_path).snapshot()
        assert len(before["artifacts"]) == 1 and before["files"] == {}
        child.kill()
        child.communicate(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    resumed = subprocess.run(
        [sys.executable, "-m", "agent_reliability"] + args,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resumed.returncode == 0, resumed.stderr
    after = ArtifactStore(tmp_path).snapshot()
    assert after["events"].count("node_attempt:retrieve_records") == 1
    assert after["events"].count("node_attempt:commit_artifact") == 2
    assert after["events"].count("artifact_committed") == 1
    assert after["files"]["summary.json"] == after["artifacts"][0]["content"]


async def test_oracle_rejects_corruption_and_incomplete_checkpoint(tmp_path):
    root, rows = await run_matrix(tmp_path, [langgraph_suite()[0]])
    snapshot = json.loads((root / rows[0]["id"] / "snapshot.json").read_text())
    for mutate, check in [
        (lambda s: s["files"].update({"summary.json": "corrupt"}), "matching_file"),
        (lambda s: s["graph"].update({"next": ["export"]}), "graph_finished"),
        (lambda s: s["graph"].update({"checkpoint_id": None}), "persisted_graph_checkpoint"),
    ]:
        changed = copy.deepcopy(snapshot)
        mutate(changed)
        grade = LangGraphAdapter().assess(
            langgraph_suite()[0], Execution(rows[0]["receipt"], changed, "control")
        )
        assert not grade.completed and not grade.checks[check]


async def test_unreached_fault_and_wrong_negative_control_fail(tmp_path):
    missed = Case(
        "missed",
        "langgraph",
        faults=(FaultRule("commit_artifact", "before", "timeout", occurrence=5),),
    )
    wrong = replace(langgraph_suite()[-1], expected_failed_checks=("one_artifact",))
    _, rows = await run_matrix(tmp_path, [missed, wrong])
    assert all(not r["scenario_passed"] for r in rows)


async def test_exhaustion_after_commit_is_not_a_safe_rejection(tmp_path):
    case = Case(
        "partial",
        "langgraph",
        faults=(FaultRule("commit_artifact", "after", "lost_ack", repeat=3),),
        expected="rejected",
    )
    _, rows = await run_matrix(tmp_path, [case])
    assert rows[0]["observed"] == "violation" and not rows[0]["scenario_passed"]
    assert not rows[0]["checks"]["no_committed_artifact"]


async def test_bad_config_has_no_side_effect(tmp_path):
    with pytest.raises(ValueError, match="Unknown configuration"):
        await run_matrix(tmp_path, [Case("bad", "langgraph", config={"unexpected": True})])
    assert list(tmp_path.iterdir()) == []


def test_core_does_not_import_optional_langgraph(tmp_path):
    script = """
import sys
from pathlib import Path
class NoGraph:
    def find_spec(self, fullname, *args):
        if fullname == "langgraph" or fullname.startswith("langgraph."):
            raise ImportError("optional dependency deliberately unavailable")
sys.meta_path.insert(0, NoGraph())
from agent_reliability.suite import registry_with_plugins
from agent_reliability.contracts import Case
registry = registry_with_plugins()
registry["artifact"].validate(Case("ok", "artifact"))
try:
    registry["langgraph"].validate(Case("missing", "langgraph"))
except ValueError as exc:
    assert "LangGraph extra" in str(exc)
else:
    raise AssertionError("missing dependency silently accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stderr
