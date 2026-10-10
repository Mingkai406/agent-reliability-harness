"""Self-contained, filterable HTML and portable Markdown over normalized results."""

import html
import re
from pathlib import Path

from .report_charts import outcome_svg


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
        links = []
        for filename, title in [
            ("result.json", "Case JSON"),
            ("traces.jsonl", "Trace"),
            ("snapshot.json", "Snapshot"),
        ]:
            if (root / row["id"] / filename).is_file():
                links.append(f"<a href='{e(row['id'])}/{filename}'>{title}</a>")
        evidence = " · ".join(links) or "Case records are included in Results JSON below."
        label = "Matched" if row["scenario_passed"] else "Unexpected"
        state = "ok" if row["scenario_passed"] else "bad"
        table.append(
            f"<tr data-app='{e(row['adapter'])}' data-result='{state}'><td><details>"
            f"<summary>{e(row['id'])}</summary><p>{e(faults)}</p><ul>{details}</ul>"
            f"<p>Fault schedule covered: {row['fault_coverage']}. "
            f"Error: {e(row['error_type'] or 'none')}.</p>"
            f"<p class='case-evidence'>{evidence}</p></details></td>"
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
    page = Path(__file__).with_name("report_template.html").read_text()
    summary = "".join(
        f"<span><strong>{value}</strong> {label}</span>"
        for value, label in [
            (f"{passed}/{len(results)}", "matched expectation"),
            (completed, "completed tasks"),
            (rejected, "rejections"),
            (detected, "detected controls"),
        ]
    )
    replacements = {
        "SUMMARY": summary,
        "MEASUREMENT": html.escape(manifest["measurement"]),
        "CHART": outcome_svg(results),
        "APPS": apps,
        "ROWS": "".join(table),
    }
    (root / "index.html").write_text(
        re.sub(r"\{\{(\w+)\}\}", lambda match: replacements[match[1]], page)
    )
