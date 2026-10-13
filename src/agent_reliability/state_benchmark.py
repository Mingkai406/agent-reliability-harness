"""Paired offline comparison of inline vs compact LangGraph state, with real restarts."""

import argparse
import json
import math
import platform
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

from .artifacts import ArtifactStore, assess_artifacts


def checkpoint_storage(path):
    with sqlite3.connect(path) as db:
        # Serialized checkpoint + pending-write payloads, not allocated DB pages.
        checkpoints = db.execute(
            "SELECT coalesce(sum(length(checkpoint)),0) FROM checkpoints"
        ).fetchone()[0]
        writes = db.execute("SELECT coalesce(sum(length(value)),0) FROM writes").fetchone()[0]
    return {
        "checkpoint_payload_bytes": checkpoints,
        "pending_write_payload_bytes": writes,
        "sqlite_file_bytes": path.stat().st_size,
    }


def benchmark(output, trials=10):
    if not 2 <= trials <= 100:
        raise ValueError("Use 2 to 100 paired trials")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for trial in range(trials):
        # Alternate order to limit a consistent first-run advantage.
        layouts = ("inline", "compact") if trial % 2 == 0 else ("compact", "inline")
        for layout in layouts:
            directory = output / f"trial-{trial:02d}-{layout}"
            args = [
                sys.executable,
                "-m",
                "agent_reliability",
                "langgraph-worker",
                "--directory",
                str(directory),
                "--case-id",
                "langgraph-restart-after-commit",
                "--state-layout",
                layout,
            ]
            first = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if first.returncode != 75:
                raise RuntimeError("Expected injected interruption: " + first.stderr)
            tick = time.perf_counter()
            second = subprocess.run(args, capture_output=True, text=True, timeout=30)
            elapsed = (time.perf_counter() - tick) * 1000
            if second.returncode:
                raise RuntimeError("Recovery failed: " + second.stderr)
            receipt = json.loads(second.stdout)
            snapshot = ArtifactStore(directory).snapshot()
            grade = assess_artifacts(snapshot, receipt)
            if not grade.completed:
                raise RuntimeError("Business-state invariants failed")
            events = snapshot["events"]
            spans = [
                json.loads(line) for line in (directory / "traces.jsonl").read_text().splitlines()
            ]
            writes = [s for s in spans if s["name"] == "checkpoint.write"]
            invocations = [s for s in spans if s["name"] == "workflow.invoke"]
            if len(invocations) != 2 or not invocations[-1]["links"]:
                raise RuntimeError("Missing cross-process trace link")
            rows.append(
                {
                    "trial": trial,
                    "layout": layout,
                    "recovery_wall_ms_including_process_start": elapsed,
                    "retrieve_attempts": events.count("node_attempt:retrieve_records"),
                    "commit_attempts": events.count("node_attempt:commit_artifact"),
                    "actual_commits": events.count("artifact_committed"),
                    "checkpoint_write_count": len(writes),
                    "checkpoint_serialization_bytes_total": sum(
                        s["attributes"]["checkpoint.serialized_bytes"] for s in writes
                    ),
                    "all_invariants_passed": grade.completed,
                    **checkpoint_storage(directory / "checkpoints.sqlite"),
                }
            )
    summary = {}
    for layout in ("inline", "compact"):
        selected = [r for r in rows if r["layout"] == layout]
        latency = sorted(r["recovery_wall_ms_including_process_start"] for r in selected)
        summary[layout] = {
            "trials": trials,
            "recovery_wall_median_ms": median(latency),
            "recovery_wall_p95_ms": latency[math.ceil(0.95 * len(latency)) - 1],
            "median_checkpoint_payload_bytes": median(
                r["checkpoint_payload_bytes"] for r in selected
            ),
            "median_pending_write_payload_bytes": median(
                r["pending_write_payload_bytes"] for r in selected
            ),
            "median_sqlite_file_bytes": median(r["sqlite_file_bytes"] for r in selected),
            "all_invariants_passed": all(r["all_invariants_passed"] for r in selected),
        }
    result = {
        "measurement": "Offline deterministic two-record workflow; fresh process recovery; "
        "no LLM calls. Timing includes interpreter/framework startup. "
        "Payload bytes are distinct from allocated SQLite file size.",
        "python": sys.version,
        "platform": platform.platform(),
        "summary": summary,
        "trials": rows,
    }
    (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# State recovery comparison",
        "",
        result["measurement"],
        "",
        "| Mode | Trials | Median recovery (ms) | p95 recovery (ms) | "
        "Checkpoint payload (bytes) | SQLite file (bytes) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, s in summary.items():
        lines.append(
            f"| {mode} | {trials} | {s['recovery_wall_median_ms']:.1f} | "
            f"{s['recovery_wall_p95_ms']:.1f} | {s['median_checkpoint_payload_bytes']} | "
            f"{s['median_sqlite_file_bytes']} |"
        )
    lines += [
        "",
        "Every run independently checks database commits, aggregate, exported bytes and receipt.",
        "Latency is reported as observed; compact state is not assumed to be faster.",
    ]
    (output / "comparison.md").write_text("\n".join(lines) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.output, args.trials)["summary"], indent=2))


if __name__ == "__main__":
    main()
