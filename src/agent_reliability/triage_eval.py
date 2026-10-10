"""Small controlled evaluation, not a held-out AI benchmark or incident-resolution study."""

import argparse
import asyncio
import copy
import json
from pathlib import Path

from .catalog import http_suite
from .suite import run_matrix
from .triage import bundle_case, diagnose, model_diagnose, verdict


def fixtures(root, cases):
    samples = []
    selected = {}
    for index, case in enumerate(cases):
        scenario, mode = case.config["scenario"], case.config["mode"]
        label = "clean"
        if mode == "baseline" and scenario != "clean":
            label = "duplicate_effect"
        elif scenario == "lost-ack":
            label = "acknowledgement_lost_recovered"
        elif scenario == "conflict":
            label = "conflicting_payload_rejected"
        bundle = bundle_case(root / case.id)
        # Only this grader sees mode, scenario and expected label.
        samples.append({"sample": f"sample-{index + 1:02d}", "expected": label, "bundle": bundle})
        selected[(mode, scenario)] = bundle
    ablations = []
    missing_ledger = copy.deepcopy(selected[("guarded", "clean")])
    missing_ledger["evidence"] = [r for r in missing_ledger["evidence"] if r["kind"] != "ledger"]
    ablations.append(("missing-ledger", missing_ledger))
    missing_log = copy.deepcopy(selected[("guarded", "lost-ack")])
    missing_log["evidence"] = [
        r for r in missing_log["evidence"] if r["kind"] in {"ledger", "response"}
    ]
    ablations.append(("missing-service-log", missing_log))
    contradiction = copy.deepcopy(selected[("guarded", "clean")])
    next(r for r in contradiction["evidence"] if r["kind"] == "response")["value"]["id"] = 999
    ablations.append(("contradictory-receipt", contradiction))
    missing_drop = copy.deepcopy(selected[("guarded", "lost-ack")])
    missing_drop["evidence"] = [
        r for r in missing_drop["evidence"] if r["kind"] != "response_not_sent"
    ]
    ablations.append(("unexplained-transport-failure", missing_drop))
    for name, bundle in ablations:
        samples.append({"sample": name, "expected": "insufficient_evidence", "bundle": bundle})
    return samples


def summarize(rows, method):
    answers = [(row["expected"], row[method]["label"]) for row in rows]
    bad = [
        (expected, predicted) for expected, predicted in answers if expected == "duplicate_effect"
    ]
    healthy = [
        (expected, predicted)
        for expected, predicted in answers
        if expected in {"clean", "conflicting_payload_rejected", "acknowledgement_lost_recovered"}
    ]
    incomplete = [
        (expected, predicted)
        for expected, predicted in answers
        if expected == "insufficient_evidence"
    ]
    answered = [
        (expected, predicted)
        for expected, predicted in answers
        if predicted != "insufficient_evidence"
    ]
    return {
        "total": len(answers),
        "correct_labels": sum(a == b for a, b in answers),
        "duplicate_effect_detected": sum(b == "duplicate_effect" for _, b in bad),
        "duplicate_effect_total": len(bad),
        "false_duplicate_alarms_on_healthy": sum(b == "duplicate_effect" for _, b in healthy),
        "healthy_total": len(healthy),
        "correct_abstentions": sum(b == "insufficient_evidence" for _, b in incomplete),
        "incomplete_total": len(incomplete),
        "answered": len(answered),
        "correct_when_answered": sum(a == b for a, b in answered),
    }


async def evaluate(output, endpoint=None, model=None):
    if bool(endpoint) != bool(model):
        raise ValueError("Both model URL and model name are required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = http_suite()
    root, results = await run_matrix(output / "raw", cases)
    if not all(row["scenario_passed"] for row in results):
        raise RuntimeError("Underlying HTTP experiment failed; refuse to score diagnoses")
    rows = fixtures(root, cases)
    for row in rows:
        row["receipt_baseline"] = diagnose(row["bundle"], receipts_only=True)
        row["evidence_rules"] = diagnose(row["bundle"])
        if endpoint:
            try:
                row["model"] = model_diagnose(row["bundle"], endpoint, model)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                row["model"] = verdict("insufficient_evidence", reason="Model call/output failure")
    methods = ["receipt_baseline", "evidence_rules"] + (["model"] if endpoint else [])
    report = {
        "schema_version": 1,
        "measurement": "8 real local HTTP/SQLite cases + 4 evidence ablations",
        "model": model,
        "model_result": "measured" if endpoint else "not measured",
        "limitations": (
            "Development fixtures, not held-out incidents. "
            "Rules define the model citation gate. No MTTR or production accuracy claim."
        ),
        "summary": {method: summarize(rows, method) for method in methods},
        "samples": rows,
    }
    (output / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-url")
    parser.add_argument("--model")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.output, args.model_url, args.model))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
