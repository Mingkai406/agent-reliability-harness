import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_reliability.experiment import grade, run_case, run_suite
from agent_reliability.runtime import Gateway, TransientFailure
from agent_reliability.scenarios import SCENARIOS, scenario_by_name
from agent_reliability.store import Conflict, Denied, Store
from agent_reliability.telemetry import provider_for


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
@pytest.mark.parametrize("mode", ["baseline", "guarded"])
async def test_fault_matrix(tmp_path, scenario, mode):
    result = await run_case(tmp_path, scenario, mode)
    baseline_failures = {"after_commit_timeout", "malformed_response", "interrupted_run"}
    assert result["passed"] == (mode == "guarded" or scenario.fault not in baseline_failures)
    if mode == "guarded":
        assert result["refund_count"] == (0 if scenario.expected == "denied" else 1)
    assert result["checks"]["fault_triggered"]


def test_concurrent_requests_commit_one_refund(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    store = Store(path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(
            pool.map(
                lambda _: Store(path).refund("tenant-a", "order-a", 2000, "same-request"), range(30)
            )
        )
    assert len({r["refund_id"] for r in receipts}) == 1
    assert len(store.snapshot()) == 1


def test_operation_key_cannot_change_amount_or_bypass_identity(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    store.refund("tenant-a", "order-a", 2000, "task-1")
    with pytest.raises(Conflict):
        store.refund("tenant-a", "order-a", 3000, "task-1")
    with pytest.raises(Denied):
        store.refund("tenant-b", "order-a", 2000, "task-1")
    assert len(store.snapshot()) == 1


def test_concurrent_refunds_cannot_exceed_balance(tmp_path):
    store = Store(tmp_path / "state.sqlite")

    def refund(i):
        try:
            return store.refund("tenant-a", "order-a", 3000, str(i))
        except Denied:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(refund, range(8)))
    assert sum(r is not None for r in receipts) == 3
    assert sum(r["amount"] for r in store.snapshot()) == 9000


def test_retry_budget_is_bounded(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    provider = provider_for(tmp_path / "trace.jsonl")
    gateway = Gateway(store, SCENARIOS[0], "guarded", provider.get_tracer("test"), backoff=0)

    def always_fail(*args):
        raise TransientFailure("Unavailable")

    monkeypatch.setattr(gateway, "_invoke", always_fail)
    assert gateway.issue_refund("order-a", 2000)["status"] == "failed"
    assert sum(e["kind"] == "tool_attempt" for e in store.events()) == 3
    assert store.snapshot() == []
    provider.shutdown()


def test_model_arguments_cannot_expand_authorized_task(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    provider = provider_for(tmp_path / "trace.jsonl")
    gateway = Gateway(store, SCENARIOS[0], "guarded", provider.get_tracer("test"))
    for order, amount in [("order-b", 2000), ("order-a", 9000), ("order-a", True)]:
        assert gateway.issue_refund(order, amount)["status"] == "denied"
    assert store.snapshot() == []
    provider.shutdown()


def test_scorer_rejects_fabricated_success(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    response = {
        "status": "refunded",
        "refund_id": "invented",
        "order_id": "order-a",
        "amount_cents": 2000,
    }
    result = grade(SCENARIOS[0], store, response)
    assert not result["checks"]["expected_final_state"]
    assert not result["checks"]["valid_completion"]


def test_subprocess_recovery_and_config_binding(tmp_path):
    path = tmp_path / "worker.sqlite"
    args = [
        sys.executable,
        "-m",
        "agent_reliability",
        "worker",
        "--db",
        str(path),
        "--scenario",
        "interrupted_after_commit",
        "--mode",
        "guarded",
    ]
    first = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert first.returncode == 75, first.stderr
    assert len(Store(path).snapshot()) == 1
    assert Store(path).get("checkpoint") is None
    second = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert second.returncode == 0, second.stderr
    receipt = json.loads(second.stdout)
    assert grade(scenario_by_name("interrupted_after_commit"), Store(path), receipt)["passed"]
    third = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert json.loads(third.stdout) == receipt
    assert len(Store(path).snapshot()) == 1
    wrong = subprocess.run(args[:-1] + ["baseline"], capture_output=True, text=True, timeout=15)
    assert wrong.returncode != 0
    assert "different scenario or mode" in wrong.stderr


async def test_reports_and_trace_parentage(tmp_path):
    root, results = await run_suite(tmp_path)
    assert len(results) == 14
    assert sum(r["passed"] for r in results if r["mode"] == "guarded") == 7
    assert sum(r["passed"] for r in results if r["mode"] == "baseline") == 4
    spans = [
        json.loads(line)
        for line in (root / "clean--guarded" / "traces.jsonl").read_text().splitlines()
    ]
    parent = next(s for s in spans if s["name"] == "case.run")
    children = [s for s in spans if s["name"].startswith("tool.")]
    assert len(children) == 2
    assert all(s["parent_id"] == parent["context"]["span_id"] for s in children)
    assert all(s["context"]["trace_id"] == parent["context"]["trace_id"] for s in children)
    assert json.loads((root / "manifest.json").read_text())["measurement"] == (
        "synthetic scripted controls"
    )
    assert (root / "index.html").is_file()
