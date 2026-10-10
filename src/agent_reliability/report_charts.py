"""Dependency-free SVG plots over recorded case results."""

import html
import math

APP_NAMES = {
    "refund": "Refund service",
    "artifact": "Artifact workflow",
    "creatorpal": "CreatorPal",
    "langgraph": "LangGraph workflow",
    "code-validation": "Code validation",
    "http-charge": "HTTP charge service",
}


def text(x, y, value, size=15, fill="#263238", anchor="start", weight=400):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{html.escape(str(value))}</text>'
    )


def svg_start(width, height, title, description):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(title)}</title>',
        f'<desc id="desc">{html.escape(description)}</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<g font-family="Arial, Helvetica, sans-serif">',
    ]


def outcome_svg(results):
    """Use a shared zero-based count axis; keep unexpected scenarios visible."""
    names = sorted({r["adapter"] for r in results})
    groups = [[r for r in results if r["adapter"] == name] for name in names]
    counts = [
        (
            sum(r["task_completed"] for r in group),
            sum(r["observed"] == "rejected" for r in group),
            sum(r["observed"] == "violation" and r["scenario_passed"] for r in group),
        )
        for group in groups
    ]
    maximum = max((max(row) for row in counts), default=0)
    limit = max(5, math.ceil(maximum / 5) * 5)
    bottom = 158 + max(len(names), 1) * 46
    height = bottom + 70
    passed = sum(r["scenario_passed"] for r in results)
    description = "; ".join(
        f"{APP_NAMES.get(name, name)}: {a} completed, {b} rejected, {c} detected controls"
        for name, (a, b, c) in zip(names, counts)
    )
    parts = svg_start(1000, height, "Recorded outcomes by application", description)
    parts += [
        text(24, 34, "Recorded outcomes by application", 23, weight=600),
        text(24, 61, "Case counts · each panel uses the same scale", 14, "#59656b"),
        text(948, 34, f"{passed}/{len(results)}", 23, anchor="end", weight=600),
        text(948, 61, "matched expectation", 13, "#59656b", "end"),
    ]
    panels = [
        (245, "Completed tasks", "#356b8c"),
        (465, "Rejections", "#9c6b28"),
        (685, "Detected controls", "#936075"),
    ]
    for column, (left, label, color) in enumerate(panels):
        parts.append(text(left, 109, label, 15, weight=600))
        for tick in (0, limit // 2, limit):
            x = left + tick / limit * 155
            parts.append(f'<path d="M{x} 137V{bottom}" stroke="#e3e7e9"/>')
            parts.append(text(x, bottom + 24, tick, 13, "#59656b", "middle"))
        for i, values in enumerate(counts):
            y = 157 + i * 46
            x = left + values[column] / limit * 155
            parts.append(f'<path d="M{left} {y}H{x}" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"/>')
            parts.append(text(x + 12, y + 5, values[column], 15, color))
    parts.append(text(952, 109, "Matched", 15, anchor="end", weight=600))
    for i, (name, group) in enumerate(zip(names, groups)):
        y = 157 + i * 46
        parts.append(text(24, y + 5, APP_NAMES.get(name, name), 15))
        match = sum(r["scenario_passed"] for r in group)
        parts.append(text(952, y + 5, f"{match}/{len(group)}", 15, anchor="end"))
    if not results:
        parts.append(text(24, 162, "No cases recorded", 15))
    parts.append(
        text(
            24,
            height - 12,
            "Detected controls are expected violations, not completed tasks.",
            13,
            "#59656b",
        )
    )
    return "\n".join([*parts, "</g></svg>"])
