"""Self-contained, filterable HTML and portable Markdown over normalized results."""

import html
from pathlib import Path


def write_suite_report(root, results, manifest):
    passed = sum(r["scenario_passed"] for r in results)
    completed = sum(r["task_completed"] for r in results)
    rejected = sum(r["observed"] == "rejected" for r in results)
    detected = sum(r["observed"] == "violation" and r["scenario_passed"] for r in results)
    lines = [
        "# Agent Reliability Harness — experiment report",
        "",
        manifest["measurement"],
        "",
        f"**{passed}/{len(results)} expected outcomes · {completed} completed tasks · "
        f"{rejected} safe rejections · {detected} detected negative controls**",
        "",
        "A passing scenario matches its declared expectation and injects every scheduled fault. "
        "An expected violation is a detected broken control, not a successful task.",
        "",
        "| Case | Application | Expected | Observed | Fault schedule covered | Scenario passed |",
        "|---|---|---|---|---|---|",
    ]
    table = []
    for row in results:
        lines.append(
            f"| {row['id']} | {row['adapter']} | {row['expected']} | {row['observed']} | "
            f"{row['fault_coverage']} | {row['scenario_passed']} |"
        )
        e = html.escape
        details = "".join(
            f"<li><span class='{'ok' if value else 'bad'}'>{'✓' if value else '✕'}</span> "
            f"{e(key.replace('_', ' '))}</li>"
            for key, value in row["checks"].items()
        )
        faults = (
            ", ".join(
                f"{f['tool']} / {f['phase']} / {f['kind']} ×{f['repeat']}" for f in row["faults"]
            )
            or "No injected fault"
        )
        label = "EXPECTED" if row["scenario_passed"] else "UNEXPECTED"
        state = "ok" if row["scenario_passed"] else "bad"
        table.append(
            f"<tr data-app='{e(row['adapter'])}' data-result='{state}'><td><details>"
            f"<summary>{e(row['id'])}</summary><p>{e(faults)}</p><ul>{details}</ul>"
            f"<p>Fault schedule covered: {row['fault_coverage']}. "
            f"Error: {e(row['error_type'] or 'none')}.</p>"
            f"<a href='{e(row['id'])}/result.json'>Case JSON</a> · "
            f"<a href='{e(row['id'])}/traces.jsonl'>Trace</a></details></td>"
            f"<td>{e(row['adapter'])}</td><td>{e(row['expected'])}</td>"
            f"<td>{e(row['observed'])}</td><td class='{state}'>{label}</td></tr>"
        )
    lines.extend(
        [
            "",
            "Local durations are diagnostic measurements, not model inference benchmarks. "
            "No model usage or cost is measured. Each case retains its snapshot, fault journal "
            "and OpenTelemetry traces.",
        ]
    )
    (root / "report.md").write_text("\n".join(lines) + "\n")
    apps = "".join(
        f"<option>{html.escape(name)}</option>" for name in sorted({r["adapter"] for r in results})
    )
    descriptions = {
        "refund": ("Refund service", "Transactions · tenant scope · idempotency"),
        "artifact": ("Artifact workflow", "Durable steps · file reconciliation · recovery"),
        "creatorpal": ("CreatorPal agent", "ADK tools · evidence · report integrity"),
        "langgraph": ("LangGraph workflow", "Disk checkpoints · node retries · restart recovery"),
    }
    coverage = ""
    for name in sorted({r["adapter"] for r in results}):
        title, description = descriptions.get(name, (name, "Custom application adapter"))
        selected = [r for r in results if r["adapter"] == name]
        coverage += (
            f"<div><strong>{html.escape(title)}</strong><span>{len(selected)} cases · "
            f"{sum(r['scenario_passed'] for r in selected)} expected outcomes</span>"
            f"<small>{html.escape(description)}</small></div>"
        )
    page = Path(__file__).with_name("report_template.html").read_text()
    cards = "".join(
        f"<div class='card'><strong>{value}</strong><span>{label}</span></div>"
        for value, label in [
            (f"{passed}/{len(results)}", "Expected outcomes"),
            (completed, "Completed tasks"),
            (rejected, "Safe rejections"),
            (detected, "Detected negative controls"),
        ]
    )
    (root / "index.html").write_text(
        page.replace("CARDS", cards)
        .replace("APPS", apps)
        .replace("COVERAGE", coverage)
        .replace("ROWS", "".join(table))
    )
