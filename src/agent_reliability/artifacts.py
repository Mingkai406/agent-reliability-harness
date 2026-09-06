"""A real local retrieve/build/commit/export workflow used as a reference application."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import Assessment
from .faults import InterruptedFault, PermanentFault, RetryableFault

RECORDS = [{"team": "alpha", "tasks": 12}, {"team": "beta", "tasks": 8}]


class ArtifactStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, content TEXT);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, kind TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def bind(self, config):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT payload FROM state WHERE key='binding'").fetchone()
            encoded = json.dumps(config, sort_keys=True)
            if prior and prior[0] != encoded:
                raise ValueError("Artifact state belongs to a different task configuration")
            db.execute("INSERT OR IGNORE INTO state VALUES ('binding',?)", (encoded,))

    def event(self, kind):
        with self.connect() as db:
            db.execute("INSERT INTO events(kind) VALUES (?)", (kind,))

    def commit(self, content):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT content FROM artifacts WHERE id='summary'").fetchone()
            if prior:
                if prior[0] != content:
                    raise ValueError("Artifact operation key reused with different content")
            else:
                db.execute("INSERT INTO artifacts VALUES ('summary',?)", (content,))
                db.execute("INSERT INTO events(kind) VALUES ('artifact_committed')")
        return {"status": "committed", "sha256": hashlib.sha256(content.encode()).hexdigest()}

    def export(self):
        with self.connect() as db:
            row = db.execute("SELECT content FROM artifacts WHERE id='summary'").fetchone()
        if row is None:
            raise ValueError("Cannot export an uncommitted artifact")
        content = row[0].encode()
        target = self.directory / "summary.json"
        # The database is the source of truth. Reconcile a missing or partial materialization.
        if not target.exists() or target.read_bytes() != content:
            fd, temporary = tempfile.mkstemp(prefix=".export-", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            self.event("artifact_exported")
        return {"status": "complete", "sha256": hashlib.sha256(content).hexdigest()}

    def snapshot(self):
        with self.connect() as db:
            artifacts = [dict(id=r[0], content=r[1]) for r in db.execute("SELECT * FROM artifacts")]
            events = [r[0] for r in db.execute("SELECT kind FROM events ORDER BY seq")]
        target = self.directory / "summary.json"
        return {
            "contract_version": 1,
            "records": self.get("records"),
            "artifacts": artifacts,
            "files": {"summary.json": target.read_text()} if target.is_file() else {},
            "checkpoint": self.get("complete"),
            "events": events,
        }


def run_workflow(store, faults, tracer):
    """One worker invocation. All retries are bounded; interruptions escape to its supervisor."""

    def tool(name, action, validate):
        with tracer.start_as_current_span("tool." + name):
            for _ in range(3):
                store.event("tool_attempt")
                try:
                    faults.apply(name, "before")
                    result = faults.apply(name, "after", action())
                    if not validate(result):
                        raise RetryableFault("invalid_response")
                    return result
                except RetryableFault:
                    store.event("retryable_failure")
            raise PermanentFault("retry_budget_exhausted")

    try:
        records = store.get("records")
        if records is None:
            records = tool("retrieve_records", lambda: RECORDS, lambda r: r == RECORDS)
            store.put("records", records)
        content = store.get("content")
        if content is None:
            content = tool(
                "build_artifact",
                lambda: json.dumps(
                    {"total_tasks": sum(r["tasks"] for r in records)}, sort_keys=True
                ),
                lambda r: isinstance(r, str) and json.loads(r).get("total_tasks") == 20,
            )
            store.put("content", content)
        tool(
            "commit_artifact",
            lambda: store.commit(content),
            lambda r: (
                isinstance(r, dict)
                and r.get("status") == "committed"
                and r.get("sha256") == hashlib.sha256(content.encode()).hexdigest()
            ),
        )
        receipt = tool(
            "export_artifact",
            store.export,
            lambda r: (
                isinstance(r, dict)
                and r.get("status") == "complete"
                and r.get("sha256") == hashlib.sha256(content.encode()).hexdigest()
            ),
        )
        store.put("complete", receipt)
        return receipt
    except PermanentFault:
        store.event("execution_rejected")
        return {"status": "failed"}
    except InterruptedFault:
        store.event("worker_interrupted")
        raise


def assess_artifacts(snapshot, receipt):
    """Independent oracle checks bytes, arithmetic, receipt and journal, not runtime validators."""
    try:
        artifacts, files = snapshot["artifacts"], snapshot["files"]
        rejected = receipt.get("status") == "failed"
        if rejected:
            checks = {
                "contract": snapshot["contract_version"] == 1,
                "no_committed_artifact": artifacts == [],
                "no_export": files == {},
                "no_completion_checkpoint": snapshot["checkpoint"] is None,
                "explicit_rejection": "execution_rejected" in snapshot["events"],
            }
            return Assessment(False, all(checks.values()), checks)
        content = artifacts[0]["content"] if len(artifacts) == 1 else "{}"
        parsed = json.loads(content)
        actual_hash = hashlib.sha256(content.encode()).hexdigest()
        checks = {
            "contract": snapshot["contract_version"] == 1,
            "one_artifact": len(artifacts) == 1 and artifacts[0]["id"] == "summary",
            "source_records": snapshot["records"] == RECORDS,
            "correct_aggregate": type(parsed.get("total_tasks")) is int
            and parsed["total_tasks"] == 20,
            "matching_file": files == {"summary.json": content},
            "single_commit": snapshot["events"].count("artifact_committed") == 1,
            "matching_receipt": receipt.get("status") == "complete"
            and receipt.get("sha256") == actual_hash,
            "completion_checkpoint": snapshot["checkpoint"] == receipt,
        }
        return Assessment(all(checks.values()), False, checks)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return Assessment(False, False, {"snapshot_schema": False})
