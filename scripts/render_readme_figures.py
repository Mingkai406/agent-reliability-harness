"""Regenerate README figures from committed evidence, without rerunning experiments.

Run from the repository root: python scripts/render_readme_figures.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_reliability.report_charts import outcome_svg, svg_start, text  # noqa: E402


def gate_matrix():
    root = ROOT / "examples/code-validation"
    rows = json.loads((root / "results.json").read_text())
    gates = ["policy", "build", "types", "unit", "integration", "static"]
    parts = svg_start(
        1000,
        510,
        "Candidate patch validation matrix",
        "Eight candidates checked against policy and five Docker gates. "
        "Pass, fail, timeout and not-run states are labeled in every cell.",
    )
    parts += [
        text(24, 34, "Where each candidate is rejected", 23, weight=600),
        text(
            24,
            61,
            "Recorded Docker checks · one valid patch and seven rejection controls",
            14,
            "#59656b",
        ),
    ]
    for i, label in enumerate(["Policy", "Build", "Types", "Unit", "Integration", "Static"]):
        parts.append(text(256 + i * 102, 108, label, 14, anchor="middle", weight=600))
    parts.append(text(950, 108, "Verdict", 14, anchor="end", weight=600))
    labels = {
        "valid": "Valid patch",
        "syntax": "Syntax error",
        "type": "Type error",
        "unit": "Wrong result",
        "integration": "CLI contract",
        "static": "Unused import",
        "timeout": "Infinite loop",
        "protected": "Protected test edit",
    }
    palette = {
        "Pass": ("#edf3f6", "#356b8c"),
        "Fail": ("#f3e5e0", "#9b4934"),
        "Timeout": ("#f6eddc", "#876019"),
        "—": ("#f4f5f5", "#69757c"),
    }
    for i, row in enumerate(rows):
        snapshot = json.loads((root / row["id"] / "snapshot.json").read_text())
        y = 128 + i * 39
        parts.append(text(24, y + 21, labels[row["id"].removeprefix("code-")], 15))
        for j, gate in enumerate(gates):
            if gate == "policy":
                state = "Pass" if snapshot["policy_passed"] else "Fail"
            elif gate not in snapshot["gates"]:
                state = "—"
            else:
                item = snapshot["gates"][gate]
                state = "Timeout" if item["timed_out"] else "Pass" if item["passed"] else "Fail"
            bg, color = palette[state]
            x = 211 + j * 102
            parts.append(f'<rect x="{x}" y="{y}" width="90" height="30" fill="{bg}"/>')
            parts.append(text(x + 45, y + 21, state, 13, color, "middle"))
        verdict = "Accepted" if row["observed"] == "completed" else "Rejected"
        parts.append(text(950, y + 21, verdict, 14, anchor="end"))
    parts += [
        text(
            24,
            470,
            "—  Not run: protected-path policy rejected the patch before execution.",
            13,
            "#59656b",
        ),
        text(
            24,
            494,
            "A candidate can fail multiple gates. Each executed gate runs in a fresh container.",
            13,
            "#59656b",
        ),
    ]
    return "\n".join([*parts, "</g></svg>"])


def main():
    assets = ROOT / "docs/assets"
    rows = json.loads((ROOT / "examples/showcase/results.json").read_text())
    (assets / "outcomes.svg").write_text(outcome_svg(rows) + "\n")
    (assets / "validation-matrix.svg").write_text(gate_matrix() + "\n")


if __name__ == "__main__":
    main()
