"""The simulated payment service owns transactions and idempotency, not the LLM."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Denied(Exception):
    pass


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, total INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS refunds (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, data TEXT NOT NULL);
                INSERT OR IGNORE INTO orders VALUES ('order-a', 'tenant-a', 10000);
                INSERT OR IGNORE INTO orders VALUES ('order-b', 'tenant-b', 10000);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def consume_fault(self):
        with self.connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO state VALUES ('fault_used', 'true')")
            return cur.rowcount == 1

    def event(self, kind, **data):
        with self.connect() as db:
            db.execute("INSERT INTO events(kind,data) VALUES (?,?)", (kind, json.dumps(data)))

    def events(self):
        with self.connect() as db:
            return [
                dict(seq=r[0], kind=r[1], **json.loads(r[2]))
                for r in db.execute("SELECT seq,kind,data FROM events ORDER BY seq")
            ]

    def order(self, actor: str, order_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row or row["tenant"] != actor:
            raise Denied("Order unavailable to this actor")
        return dict(row)

    def refund(self, actor, order_id, amount, operation_key=None):
        if type(amount) is not int or amount <= 0:
            raise ValueError("amount_cents must be a positive integer")
        # BEGIN IMMEDIATE serializes duplicate requests across separate connections/processes.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order or order["tenant"] != actor:
                raise Denied("Order unavailable to this actor")
            payload = json.dumps([actor, order_id, amount])
            if operation_key:
                prior = db.execute(
                    "SELECT * FROM operations WHERE key=?", (operation_key,)
                ).fetchone()
                if prior:
                    if prior["payload"] != payload:
                        raise Conflict("Operation key reused with different arguments")
                    return json.loads(prior["result"])
            total = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM refunds WHERE order_id=?", (order_id,)
            ).fetchone()[0]
            if total + amount > order["total"]:
                raise Denied("Refund exceeds remaining balance")
            result = {
                "status": "refunded",
                "refund_id": str(uuid.uuid4()),
                "order_id": order_id,
                "amount_cents": amount,
            }
            db.execute(
                "INSERT INTO refunds VALUES (?,?,?)", (result["refund_id"], order_id, amount)
            )
            if operation_key:
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?)",
                    (operation_key, payload, json.dumps(result)),
                )
            return result

    def snapshot(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM refunds ORDER BY rowid")]
