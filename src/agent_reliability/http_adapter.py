"""Real loopback HTTP + SQLite boundary; deterministic faults, no model inference."""

import asyncio
import http.client
import json
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import Assessment, Execution
from .faults import FaultEngine, FaultRule, RetryableFault


def serve(directory):
    directory = Path(directory)
    config = json.loads((directory / "service.json").read_text())
    faults = FaultEngine(directory / "faults.sqlite", [FaultRule(**r) for r in config["faults"]])
    database = directory / "ledger.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, key TEXT, amount INTEGER)")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/charges":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                self.send_error(400)
                return
            try:
                payload = json.loads(self.rfile.read(length))
                key, amount = payload["key"], payload["amount"]
                if not isinstance(key, str) or type(amount) is not int or amount <= 0:
                    raise ValueError("invalid charge")
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            status = 200
            db = sqlite3.connect(database, timeout=5)
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    prior = db.execute(
                        "SELECT id, amount FROM ledger WHERE key=?", (key,)
                    ).fetchone()
                    if prior and config["mode"] == "guarded":
                        identity = prior[0]
                        if prior[1] != amount:
                            status = 409
                    else:
                        identity = db.execute(
                            "INSERT INTO ledger(key,amount) VALUES (?,?)", (key, amount)
                        ).lastrowid
            finally:
                db.close()
            # This is after the real database commit and before HTTP response headers.
            try:
                faults.apply("http_charge", "after")
            except RetryableFault:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            body = json.dumps({"id": identity, "amount": amount}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    (directory / "ready.json").write_text(json.dumps({"port": server.server_port}))
    server.serve_forever(poll_interval=0.05)


def request_charge(port, amount=25):
    attempts = []
    for _ in range(3):
        request = Request(
            f"http://127.0.0.1:{port}/charges",
            data=json.dumps({"key": "charge-1", "amount": amount}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=2) as response:
                result = json.loads(response.read())
                attempts.append("acknowledged")
                return {"status": response.status, **result, "attempts": attempts}
        except HTTPError as error:
            return {"status": error.code, "attempts": attempts + ["http_error"]}
        except (URLError, http.client.RemoteDisconnected, ConnectionError, TimeoutError):
            attempts.append("transport_failure")
    return {"status": 0, "attempts": attempts}


class HttpChargeAdapter:
    def validate(self, case):
        if set(case.config) != {"mode", "scenario"}:
            raise ValueError("HTTP config requires mode and scenario")
        if case.config["mode"] not in {"guarded", "baseline"}:
            raise ValueError("Unknown HTTP mode")
        if case.config["scenario"] not in {"clean", "lost-ack", "concurrent", "conflict"}:
            raise ValueError("Unknown HTTP scenario")
        expected = (FaultRule("http_charge", "after", "lost_ack"),)
        if case.faults != (expected if case.config["scenario"] == "lost-ack" else ()):
            raise ValueError("HTTP scenario requires its exact transport fault schedule")

    async def execute(self, case, directory, faults, tracer):
        return await asyncio.to_thread(self._execute, case, directory)

    def _execute(self, case, directory):
        config = {**case.config, "faults": case.to_dict()["faults"]}
        (directory / "service.json").write_text(json.dumps(config))
        process = subprocess.Popen(
            [sys.executable, "-m", __name__, str(directory)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            ready = directory / "ready.json"
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("HTTP service did not start")
                time.sleep(0.01)
            # Read after the short readiness write completes.
            while True:
                try:
                    port = json.loads(ready.read_text())["port"]
                    break
                except json.JSONDecodeError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Incomplete readiness record") from None
                    time.sleep(0.01)
            scenario = case.config["scenario"]
            if scenario == "concurrent":
                with ThreadPoolExecutor(max_workers=4) as pool:
                    receipts = list(pool.map(lambda _: request_charge(port), range(4)))
            else:
                receipts = [request_charge(port)]
                if scenario == "conflict":
                    receipts.append(request_charge(port, 99))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # Independent oracle reads the durable DB, not a service-provided snapshot.
        db = sqlite3.connect(directory / "ledger.sqlite")
        try:
            rows = [dict(id=i, key=k, amount=a) for i, k, a in db.execute("SELECT * FROM ledger")]
        finally:
            db.close()
        return Execution(
            {"responses": receipts},
            {"ledger": rows},
            "Local HTTP subprocess + SQLite; controlled connection loss; no model calls",
        )

    def assess(self, case, execution):
        rows = execution.snapshot["ledger"]
        responses = execution.receipt["responses"]
        expected_statuses = (
            [200, 409] if case.config["scenario"] == "conflict" else [200] * len(responses)
        )
        checks = {
            "one_effect": len(rows) == 1,
            "authorized_amount": bool(rows) and all(row["amount"] == 25 for row in rows),
            "response_statuses": [r["status"] for r in responses] == expected_statuses,
            "receipt_matches_state": all(
                any(row["id"] == r.get("id") and row["amount"] == r.get("amount") for row in rows)
                for r in responses
                if r["status"] == 200
            ),
        }
        return Assessment(all(checks.values()), False, checks)


if __name__ == "__main__":
    serve(sys.argv[1])
