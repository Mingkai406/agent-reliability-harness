import json
import os

import pytest

from agent_reliability.code_demo import diff, prepare, run
from agent_reliability.code_validation import patch_paths
from agent_reliability.contracts import Case, Execution
from agent_reliability.faults import FaultEngine
from agent_reliability.telemetry import provider_for


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", ".git/config", "src/../../bad", "src/a b", "src/a\\b"]
)
def test_unsafe_patch_paths_rejected(path):
    with pytest.raises(ValueError):
        patch_paths(diff("a\n", "b\n", path))


def test_empty_and_mode_changes_rejected():
    for patch in ("", "new file mode 120000\n" + diff("a\n", "b\n")):
        with pytest.raises(ValueError):
            patch_paths(patch)


async def test_protected_tests_rejected_before_execution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter, cases = prepare(repo)
    directory = tmp_path / "run"
    directory.mkdir()
    provider = provider_for(directory / "traces.jsonl")
    try:
        execution = await adapter.execute(
            cases[-1],
            directory,
            FaultEngine(directory / "faults.sqlite", (), provider.get_tracer("test")),
            provider.get_tracer("test"),
        )
        assert adapter.assess(cases[-1], execution).safely_rejected
        assert execution.snapshot["gates"] == {}
        assert not execution.snapshot["policy_passed"]
    finally:
        provider.shutdown()


def test_false_acceptance_and_missing_checks_fail(tmp_path):
    adapter, cases = prepare(tmp_path)
    snapshot = {"policy_passed": True, "gates": {}}
    assessment = adapter.assess(cases[0], Execution({"status": "accepted"}, snapshot, "test"))
    assert not assessment.completed and not assessment.safely_rejected
    with pytest.raises(ValueError):
        adapter.validate(Case("bad", "code-validation", {"candidate": "unknown"}))


@pytest.mark.skipif(os.environ.get("HARNESS_DOCKER_TEST") != "1", reason="requires validator image")
async def test_real_docker_matrix(tmp_path):
    root, results = await run(tmp_path)
    assert len(results) == 8
    assert sum(r["task_completed"] for r in results) == 1
    snapshot = json.loads((root / "code-timeout" / "snapshot.json").read_text())
    assert snapshot["gates"]["unit"]["timed_out"]
