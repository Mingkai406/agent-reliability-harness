import time

from .store import Conflict, Denied


class TransientFailure(Exception):
    pass


class InvalidResponse(Exception):
    pass


class InterruptedRun(Exception):
    """Injected worker interruption; the caller must resume using the durable store."""


class Gateway:
    """Shared tool boundary used by scripted and ADK agents.

    Baseline has retries but no operation key or response validation. Guarded enables both.
    Identity and the authorized business operation are bound outside the model's arguments.
    """

    def __init__(self, store, scenario, mode, tracer, max_attempts=3, backoff=0.001):
        if mode not in {"baseline", "guarded"}:
            raise ValueError("unknown mode")
        self.store, self.scenario, self.mode = store, scenario, mode
        self.tracer, self.max_attempts, self.backoff = tracer, max_attempts, backoff

    def lookup_order(self, order_id: str) -> dict:
        with self.tracer.start_as_current_span("tool.lookup_order"):
            try:
                return self.store.order(self.scenario.actor, order_id)
            except Denied:
                self.store.event("denied", tool="lookup_order")
                return {"status": "denied", "error": "Order unavailable to this actor"}

    def issue_refund(self, order_id: str, amount_cents: int) -> dict:
        """Refund the authorized amount. Retry with identical arguments after transient errors."""
        if (
            order_id != self.scenario.order
            or type(amount_cents) is not int
            or amount_cents != self.scenario.amount_cents
        ):
            self.store.event("denied", tool="issue_refund", reason="outside_task_scope")
            return {"status": "denied", "error": "Arguments exceed authorized task scope"}
        with self.tracer.start_as_current_span("tool.issue_refund") as span:
            span.set_attribute("harness.mode", self.mode)
            for attempt in range(1, self.max_attempts + 1):
                self.store.event("tool_attempt", attempt=attempt)
                try:
                    result = self._invoke(order_id, amount_cents)
                    if self.mode == "guarded" and not self._valid(result, order_id, amount_cents):
                        raise InvalidResponse("Refund tool response violated the contract")
                    span.set_attribute("harness.attempts", attempt)
                    return result
                except (Denied, Conflict, ValueError) as exc:
                    self.store.event("denied", tool="issue_refund", reason=type(exc).__name__)
                    return {"status": "denied", "error": str(exc)}
                except (TransientFailure, InvalidResponse) as exc:
                    self.store.event("retryable_failure", reason=type(exc).__name__)
                    span.add_event("retryable_failure", {"failure.type": type(exc).__name__})
                    if attempt == self.max_attempts:
                        return {"status": "failed", "error": "Tool retry budget exhausted"}
                    time.sleep(self.backoff * 2 ** (attempt - 1))
            raise AssertionError("unreachable")

    def _invoke(self, order_id, amount_cents):
        self.store.order(self.scenario.actor, order_id)  # auth before injecting a fault
        inject = self.scenario.fault != "none" and self.store.consume_fault()
        fault = self.scenario.fault if inject else "none"
        if inject:
            self.store.event("fault_injected", fault=fault)
        if fault in {"before_timeout", "rate_limit"}:
            raise TransientFailure(fault)
        key = "authorized-refund" if self.mode == "guarded" else None
        result = self.store.refund(self.scenario.actor, order_id, amount_cents, key)
        if fault == "after_commit_timeout":
            raise TransientFailure("Service committed but acknowledgement was lost")
        if fault == "malformed_response":
            return {"status": "ok", "receipt": None}
        if fault == "interrupted_run":
            raise InterruptedRun("Interrupted after commit, before agent checkpoint")
        return result

    @staticmethod
    def _valid(result, order_id, amount):
        return (
            isinstance(result, dict)
            and result.get("status") == "refunded"
            and isinstance(result.get("refund_id"), str)
            and bool(result["refund_id"])
            and result.get("order_id") == order_id
            and type(result.get("amount_cents")) is int
            and result["amount_cents"] == amount
        )


def run_scripted(gateway):
    """Deterministic tool client, deliberately NOT a model intelligence benchmark."""
    store = gateway.store
    checkpoint = store.get("checkpoint", {}) if gateway.mode == "guarded" else {}
    if checkpoint.get("complete"):
        return checkpoint["result"]
    order = gateway.lookup_order(gateway.scenario.order)
    if order.get("status") == "denied":
        return order
    result = gateway.issue_refund(gateway.scenario.order, gateway.scenario.amount_cents)
    if gateway.mode == "guarded" and result.get("status") in {"refunded", "denied"}:
        store.set("checkpoint", {"complete": True, "result": result})
    return result
