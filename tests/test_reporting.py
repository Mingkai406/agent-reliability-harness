"""Report regressions for incomplete exports, unexpected outcomes and untrusted labels."""

import json
from pathlib import Path

from agent_reliability.reporting import write_suite_report

REPO = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((REPO / "examples/showcase/results.json").read_text())[0]


def test_report_links_only_to_retained_case_evidence(tmp_path):
    row = sample()
    case = tmp_path / row["id"]
    case.mkdir()
    (case / "snapshot.json").write_text("{}")
    write_suite_report(tmp_path, [row], {"measurement": "Recorded controls"})
    page = (tmp_path / "index.html").read_text()
    assert f"href='{row['id']}/snapshot.json'" in page
    assert f"href='{row['id']}/result.json'" not in page
    assert f"href='{row['id']}/traces.jsonl'" not in page
    assert 'href="results.json"' in page


def test_unexpected_violation_is_not_counted_as_detected_control(tmp_path):
    row = sample()
    row.update(observed="violation", scenario_passed=False, task_completed=False)
    row["adapter"] = '<script>alert("x")</script>{{ROWS}}'
    write_suite_report(tmp_path, [row], {"measurement": "<b>untrusted</b>"})
    page = (tmp_path / "index.html").read_text()
    assert "<strong>0/1</strong> matched expectation" in page
    assert "<strong>0</strong> detected controls" in page
    assert "data-result='bad'" in page
    assert "<b>untrusted</b>" not in page
    assert "&lt;b&gt;untrusted&lt;/b&gt;" in page
    assert "<script>alert(" not in page
    assert "{{ROWS}}" in page  # User text must not be interpreted as a template token.


def test_empty_suite_has_a_valid_zero_count_report(tmp_path):
    write_suite_report(tmp_path, [], {"measurement": "No cases"})
    page = (tmp_path / "index.html").read_text()
    assert "<strong>0/0</strong>" in page
    assert "No cases recorded" in page
    assert "No cases match these filters" in page
