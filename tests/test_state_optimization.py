import json
from dataclasses import replace

import pytest

pytest.importorskip("langgraph.graph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from agent_reliability.catalog import langgraph_suite
from agent_reliability.state_benchmark import benchmark
from agent_reliability.suite import run_matrix


async def test_compact_matrix_preserves_all_expected_outcomes(tmp_path):
    cases = [replace(c, config={**c.config, "state_layout": "compact"}) for c in langgraph_suite()]
    root, rows = await run_matrix(tmp_path, cases)
    assert len(rows) == 10 and all(r["scenario_passed"] for r in rows)
    for row in rows:
        snapshot = json.loads((root / row["id"] / "snapshot.json").read_text())
        assert snapshot["graph"]["values"].get("records") is None
    spans = [
        json.loads(line)
        for line in (root / "langgraph-lost-ack" / "traces.jsonl").read_text().splitlines()
    ]
    names = {s["name"] for s in spans}
    assert {
        "checkpoint.read",
        "checkpoint.write",
        "checkpoint.pending_writes",
        "workflow.invoke",
        "state.finish",
    } <= names
    attempts = [
        s["attributes"]["tool.attempt"] for s in spans if s["name"] == "tool.commit_artifact"
    ]
    assert attempts == [1, 2]
    assert all(
        s["attributes"]["checkpoint.serialized_bytes"] > 0
        for s in spans
        if s["name"] == "checkpoint.write"
    )
    assert all("records" not in s.get("attributes", {}) for s in spans)


def test_paired_benchmark_checks_actual_storage_and_recovery(tmp_path):
    result = benchmark(tmp_path / "comparison", trials=2)
    assert len(result["trials"]) == 4
    for row in result["trials"]:
        assert row["all_invariants_passed"]
        assert row["retrieve_attempts"] == 1
        assert row["commit_attempts"] == 2
        assert row["actual_commits"] == 1
    a, b = result["summary"]["inline"], result["summary"]["compact"]
    assert b["median_checkpoint_payload_bytes"] < a["median_checkpoint_payload_bytes"]
    # Do not assert latency speedup: process scheduling and storage noise are real.
    assert (tmp_path / "comparison" / "comparison.md").is_file()


def test_benchmark_cannot_overwrite_output(tmp_path):
    with pytest.raises(FileExistsError):
        benchmark(tmp_path, 2)
