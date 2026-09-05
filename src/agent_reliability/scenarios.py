from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    fault: str = "none"
    actor: str = "tenant-a"
    order: str = "order-a"
    amount_cents: int = 2000
    expected: str = "refunded"


SCENARIOS = (
    Scenario("clean"),
    Scenario("timeout_before_commit", "before_timeout"),
    Scenario("rate_limited", "rate_limit"),
    Scenario("lost_response_after_commit", "after_commit_timeout"),
    Scenario("malformed_response_after_commit", "malformed_response"),
    Scenario("interrupted_after_commit", "interrupted_run"),
    Scenario("cross_tenant_request", order="order-b", expected="denied"),
)


def scenario_by_name(name: str) -> Scenario:
    return next(s for s in SCENARIOS if s.name == name)
