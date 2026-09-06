"""Application-independent, durable fault schedules at tool boundaries."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


class RetryableFault(Exception):
    pass


class PermanentFault(Exception):
    pass


class InterruptedFault(Exception):
    pass


@dataclass(frozen=True)
class FaultRule:
    tool: str
    phase: str
    kind: str
    occurrence: int = 1
    repeat: int = 1

    def __post_init__(self):
        allowed = {
            "before": {"timeout", "rate_limit", "interrupt", "permanent_error"},
            "after": {"lost_ack", "malformed", "interrupt"},
        }
        if not isinstance(self.tool, str) or not self.tool or len(self.tool) > 100:
            raise ValueError("Fault tool must be a nonempty name of at most 100 characters")
        if self.phase not in allowed or self.kind not in allowed[self.phase]:
            raise ValueError("Unsupported fault kind/phase combination")
        for value in (self.occurrence, self.repeat):
            if type(value) is not int or not 1 <= value <= 100:
                raise ValueError("Fault occurrence and repeat must be integers from 1 to 100")


class FaultEngine:
    """The runner injects faults; the application owns retries and recovery.

    SQLite serializes schedule claims across processes. Markers survive a worker restart.
    This is a controlled exception injector, not a network proxy or power-loss simulator.
    """

    def __init__(self, path: Path, rules=(), tracer=None):
        self.path, self.rules, self.tracer = Path(path), tuple(rules), tracer
        targets = [(r.tool, r.phase) for r in self.rules]
        if len(set(targets)) != len(targets):
            raise ValueError("Use one rule per tool/phase; use repeat for repeated faults")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = json.dumps([asdict(r) for r in self.rules], sort_keys=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY, config TEXT);
                CREATE TABLE IF NOT EXISTS calls (rule INTEGER PRIMARY KEY, count INTEGER);
                CREATE TABLE IF NOT EXISTS faults (
                    seq INTEGER PRIMARY KEY, rule INTEGER, occurrence INTEGER, payload TEXT);
            """)
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT config FROM binding WHERE id=1").fetchone()
            if prior and prior[0] != config:
                raise ValueError("Fault journal belongs to a different schedule")
            db.execute("INSERT OR IGNORE INTO binding VALUES (1, ?)", (config,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def apply(self, tool, phase, result=None):
        for index, rule in enumerate(self.rules):
            if (rule.tool, rule.phase) != (tool, phase):
                continue
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR IGNORE INTO calls VALUES (?, 0)", (index,))
                db.execute("UPDATE calls SET count=count+1 WHERE rule=?", (index,))
                count = db.execute("SELECT count FROM calls WHERE rule=?", (index,)).fetchone()[0]
                fired = rule.occurrence <= count < rule.occurrence + rule.repeat
                if fired:
                    db.execute(
                        "INSERT INTO faults(rule,occurrence,payload) VALUES (?,?,?)",
                        (index, count, json.dumps(asdict(rule))),
                    )
            if not fired:
                continue
            if self.tracer:
                with self.tracer.start_as_current_span("fault.inject") as span:
                    span.set_attribute("fault.tool", tool)
                    span.set_attribute("fault.phase", phase)
                    span.set_attribute("fault.kind", rule.kind)
                    span.set_attribute("fault.occurrence", count)
            if rule.kind in {"timeout", "rate_limit", "lost_ack"}:
                raise RetryableFault(rule.kind)
            if rule.kind == "interrupt":
                raise InterruptedFault(rule.kind)
            if rule.kind == "permanent_error":
                raise PermanentFault(rule.kind)
            return {"harness_invalid_response": True}
        return result

    def events(self):
        with self.connect() as db:
            return [
                {**json.loads(r[2]), "rule": r[0], "occurrence": r[1]}
                for r in db.execute("SELECT rule,occurrence,payload FROM faults ORDER BY seq")
            ]

    def covered(self):
        events = self.events()
        return all(
            sum(e["rule"] == i for e in events) == r.repeat for i, r in enumerate(self.rules)
        )
