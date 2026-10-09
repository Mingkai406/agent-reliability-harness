import pytest

from agent_reliability.catalog import http_suite
from agent_reliability.contracts import Case
from agent_reliability.http_adapter import HttpChargeAdapter
from agent_reliability.suite import run_matrix


@pytest.mark.asyncio
async def test_real_http_faults_and_independent_state_oracle(tmp_path):
    _, results = await run_matrix(tmp_path, http_suite())
    assert len(results) == 8
    assert all(r["scenario_passed"] for r in results)
    for row in results:
        if row["id"].endswith("lost-ack"):
            assert row["receipt"]["responses"][0]["attempts"] == [
                "transport_failure",
                "acknowledged",
            ]
            assert row["fault_coverage"]


def test_reject_mismatched_transport_schedule():
    with pytest.raises(ValueError):
        HttpChargeAdapter().validate(
            Case("bad", "http-charge", {"mode": "guarded", "scenario": "lost-ack"})
        )
