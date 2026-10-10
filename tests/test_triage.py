import copy
import json

import pytest

from agent_reliability.catalog import http_suite
from agent_reliability.suite import run_matrix
from agent_reliability.triage import (
    bundle_case,
    diagnose,
    model_diagnose,
    triage_run,
    validate_prediction,
)


def evidence(kind, value, identity):
    return {"kind": kind, "value": value, "evidence_id": identity}


def clean_bundle():
    return {
        "evidence": [
            evidence("ledger", [{"id": 1, "key": "k", "amount": 25}], "E001"),
            evidence(
                "response",
                {
                    "status": 200,
                    "id": 1,
                    "key": "k",
                    "amount": 25,
                    "requested_amount": 25,
                    "attempts": ["acknowledged"],
                },
                "E002",
            ),
            evidence(
                "transaction",
                {
                    "attempt": "a",
                    "id": 1,
                    "key": "k",
                    "status": 200,
                    "requested_amount": 25,
                    "stored_amount": 25,
                    "inserted": True,
                },
                "E003",
            ),
        ]
    }


@pytest.mark.asyncio
async def test_live_http_triage_and_blind_inputs(tmp_path):
    root, results = await run_matrix(tmp_path / "run", http_suite())
    assert all(r["scenario_passed"] for r in results)
    reports = triage_run(root, tmp_path / "triage")
    assert len(reports) == 8
    for row in reports:
        name = row["case"]
        # Ground truth is available to this grader only, never to diagnose().
        if "baseline" in name and not name.endswith("clean"):
            expected = "duplicate_effect"
        elif name.endswith("lost-ack"):
            expected = "acknowledgement_lost_recovered"
        elif name.endswith("conflict"):
            expected = "conflicting_payload_rejected"
        else:
            expected = "clean"
        assert row["evidence_rules"]["label"] == expected
        bundle = row["bundle"]
        encoded = json.dumps(bundle)
        for forbidden in (
            name,
            "fault_events",
            "expected",
            "guarded",
            "baseline",
            "scenario_passed",
        ):
            assert forbidden not in encoded
        assert row["evidence_rules"]["citations"]
        assert validate_prediction(bundle, row["evidence_rules"])["label"] == expected
        # Replacing hidden labels must not change the evidence or diagnosis.
        result_path = root / name / "result.json"
        original = json.loads(result_path.read_text())
        original.update(expected="invented", checks={}, fault_events=[], faults=[])
        result_path.write_text(json.dumps(original))
        assert bundle_case(root / name) == bundle
    with pytest.raises(FileExistsError):
        triage_run(root, tmp_path / "triage")


@pytest.mark.parametrize("missing", ["ledger", "response", "transaction"])
def test_missing_evidence_abstains(missing):
    b = clean_bundle()
    b["evidence"] = [r for r in b["evidence"] if r["kind"] != missing]
    assert diagnose(b)["abstained"]


def test_transport_error_without_commit_trace_does_not_guess():
    b = clean_bundle()
    b["evidence"][1]["value"]["attempts"].insert(0, "transport_failure")
    assert diagnose(b)["abstained"]


def test_contradictory_receipt_abstains():
    b = clean_bundle()
    b["evidence"][1]["value"]["id"] = 999
    assert diagnose(b)["abstained"]


def test_409_alone_does_not_prove_a_conflicting_payload():
    b = clean_bundle()
    b["evidence"][1]["value"]["status"] = 409
    b["evidence"][2]["value"]["status"] = 409
    assert diagnose(b)["abstained"]


def test_duplicate_effect_is_visible_even_when_all_receipts_succeed():
    b = clean_bundle()
    b["evidence"][0]["value"].append({"id": 2, "key": "k", "amount": 25})
    assert diagnose(b, receipts_only=True)["label"] == "clean"
    assert diagnose(b)["label"] == "duplicate_effect"


@pytest.mark.parametrize(
    "proposal",
    [
        None,
        {"label": "made_up", "citations": []},
        {"label": "clean", "citations": ["E999"]},
        {"label": "clean", "citations": ["E001"]},
        {"label": "duplicate_effect", "citations": ["E001", "E002", "E003"]},
        {"label": "clean", "citations": [123]},
    ],
)
def test_model_output_requires_supported_citations(proposal):
    assert validate_prediction(clean_bundle(), proposal)["abstained"]


def test_model_opt_in_mocked_transport(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"label": "clean", "citations": ["E001", "E002", "E003"]}
                                )
                            }
                        }
                    ]
                }
            ).encode()

    def call(request, timeout):
        payload = json.loads(request.data)
        assert payload["model"] == "test-double"
        assert json.loads(payload["messages"][1]["content"]) == clean_bundle()
        return Response()

    monkeypatch.setattr("agent_reliability.triage._open_model", call)
    assert (
        model_diagnose(clean_bundle(), "http://localhost:1234/v1/chat/completions", "test-double")[
            "label"
        ]
        == "clean"
    )
    with pytest.raises(ValueError):
        model_diagnose(clean_bundle(), "http://example.com/v1/chat/completions", "unused")


def test_empty_ledger_and_unresolved_request_abstain():
    b = clean_bundle()
    original = copy.deepcopy(b)
    b["evidence"][0]["value"] = []
    assert diagnose(b)["abstained"]
    original["evidence"][1]["value"]["status"] = 0
    assert diagnose(original)["abstained"]


def test_service_ledger_contradiction_abstains():
    b = clean_bundle()
    b["evidence"][2]["value"]["stored_amount"] = 99
    assert diagnose(b)["abstained"]


def test_invalid_model_label_type_abstains():
    assert validate_prediction(clean_bundle(), {"label": [], "citations": []})["abstained"]


@pytest.mark.asyncio
async def test_evaluation_counts_and_ablation_denominators(tmp_path):
    from agent_reliability.triage_eval import evaluate

    report = await evaluate(tmp_path / "evaluation")
    assert report["model_result"] == "not measured"
    assert report["summary"]["evidence_rules"]["correct_labels"] == 12
    assert report["summary"]["receipt_baseline"]["duplicate_effect_detected"] == 0
    assert report["summary"]["evidence_rules"]["correct_abstentions"] == 4
