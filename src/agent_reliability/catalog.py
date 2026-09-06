"""Runnable controls; each application's grader defines its business invariants."""

from .contracts import Case
from .faults import FaultRule


def builtin_suite(profile="core"):
    if profile not in {"core", "full", "langgraph"}:
        raise ValueError("Unknown suite profile")
    if profile == "langgraph":
        return langgraph_suite()
    cases = []
    refund_faults = [
        ("clean", None),
        ("timeout", ("before", "timeout")),
        ("rate-limit", ("before", "rate_limit")),
        ("lost-ack", ("after", "lost_ack")),
        ("malformed", ("after", "malformed")),
        ("restart", ("after", "interrupt")),
        ("tenant-denial", None),
    ]
    for mode in ("baseline", "guarded"):
        for name, fault in refund_faults:
            expected = "completed"
            if mode == "baseline" and name in {"lost-ack", "malformed", "restart"}:
                expected = "violation"
            if name == "tenant-denial":
                expected = "rejected"
            cases.append(
                Case(
                    f"refund-{mode}-{name}",
                    "refund",
                    {"mode": mode, "order": "order-b" if name == "tenant-denial" else "order-a"},
                    (FaultRule("issue_refund", *fault),) if fault else (),
                    expected,
                    ("receipt_matches_state",)
                    if expected == "violation" and name == "malformed"
                    else ("one_refund", "authorized_amount")
                    if expected == "violation"
                    else (),
                )
            )
    artifact_faults = [
        ("clean", None),
        ("retrieval-timeout", FaultRule("retrieve_records", "before", "timeout")),
        ("rate-limit", FaultRule("retrieve_records", "before", "rate_limit")),
        ("malformed-data", FaultRule("retrieve_records", "after", "malformed")),
        ("malformed-build", FaultRule("build_artifact", "after", "malformed")),
        ("lost-ack", FaultRule("commit_artifact", "after", "lost_ack")),
        ("restart-before-commit", FaultRule("commit_artifact", "before", "interrupt")),
        ("restart-after-commit", FaultRule("commit_artifact", "after", "interrupt")),
        ("restart-after-export", FaultRule("export_artifact", "after", "interrupt")),
        ("retry-exhaustion", FaultRule("retrieve_records", "before", "timeout", repeat=3)),
    ]
    for name, fault in artifact_faults:
        cases.append(
            Case(
                f"artifact-{name}",
                "artifact",
                faults=(fault,) if fault else (),
                expected="rejected" if name == "retry-exhaustion" else "completed",
            )
        )
    if profile == "full":
        creator_faults = [
            ("clean", None),
            ("retrieval-timeout", FaultRule("search_communities", "before", "timeout")),
            ("malformed-retrieval", FaultRule("search_communities", "after", "malformed")),
            ("analytics-failure", FaultRule("run_analysis", "before", "permanent_error")),
            ("restart-before-publish", FaultRule("publish_report", "before", "interrupt")),
            ("lost-ack", FaultRule("publish_report", "after", "lost_ack")),
            ("restart-after-publish", FaultRule("publish_report", "after", "interrupt")),
            ("false-completion", None),
        ]
        for name, fault in creator_faults:
            cases.append(
                Case(
                    f"creatorpal-{name}",
                    "creatorpal",
                    {
                        "driver": "offline-adk",
                        "model_behavior": "false_completion"
                        if name == "false-completion"
                        else "normal",
                    },
                    (fault,) if fault else (),
                    "violation"
                    if name == "false-completion"
                    else "rejected"
                    if name == "analytics-failure"
                    else "completed",
                    ("one_report",) if name == "false-completion" else (),
                )
            )
    if profile == "full":
        cases.extend(langgraph_suite())
    return cases


def langgraph_suite():
    faults = [
        ("clean", None),
        ("retrieval-timeout", FaultRule("retrieve_records", "before", "timeout")),
        ("malformed-build", FaultRule("build_artifact", "after", "malformed")),
        ("lost-ack", FaultRule("commit_artifact", "after", "lost_ack")),
        ("restart-before-commit", FaultRule("commit_artifact", "before", "interrupt")),
        ("restart-after-commit", FaultRule("commit_artifact", "after", "interrupt")),
        ("restart-after-export", FaultRule("export_artifact", "after", "interrupt")),
        ("retry-exhaustion", FaultRule("retrieve_records", "before", "timeout", repeat=3)),
        ("permanent-failure", FaultRule("retrieve_records", "before", "permanent_error")),
        ("invalid-receipt", None),
    ]
    return [
        Case(
            "langgraph-" + name,
            "langgraph",
            {"mode": "invalid_receipt" if name == "invalid-receipt" else "normal"},
            (fault,) if fault else (),
            "violation"
            if name == "invalid-receipt"
            else "rejected"
            if name in {"retry-exhaustion", "permanent-failure"}
            else "completed",
            ("matching_receipt", "completion_checkpoint") if name == "invalid-receipt" else (),
        )
        for name, fault in faults
    ]
