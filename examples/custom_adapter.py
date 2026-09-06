"""Minimal independently runnable plugin: an idempotent key/value write."""

import sqlite3

from agent_reliability.contracts import Assessment, Execution
from agent_reliability.faults import RetryableFault


class KeyValueAdapter:
    def validate(self, case):
        if case.config or any(
            (r.tool, r.phase, r.kind) != ("save", "after", "lost_ack") for r in case.faults
        ):
            raise ValueError("This example supports only lost acknowledgements after save")

    async def execute(self, case, directory, faults, tracer):
        db = sqlite3.connect(directory / "state.sqlite")
        receipt = {"status": "failed"}
        try:
            with db:
                db.execute("CREATE TABLE items (id TEXT PRIMARY KEY, value TEXT)")
            with tracer.start_as_current_span("tool.save"):
                for _ in range(3):
                    try:
                        faults.apply("save", "before")
                        with db:
                            db.execute("INSERT OR IGNORE INTO items VALUES ('task-1','saved')")
                        receipt = faults.apply("save", "after", {"status": "complete"})
                        break
                    except RetryableFault:
                        continue
            snapshot = {"items": [list(row) for row in db.execute("SELECT * FROM items")]}
        finally:
            db.close()
        return Execution(receipt, snapshot, "scripted local SQLite tool")

    def assess(self, case, execution):
        checks = {
            "one_correct_write": execution.snapshot["items"] == [["task-1", "saved"]],
            "valid_receipt": execution.receipt == {"status": "complete"},
        }
        return Assessment(all(checks.values()), False, checks)


def create_adapter():
    return KeyValueAdapter()
