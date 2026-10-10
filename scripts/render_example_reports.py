"""Refresh report presentation; preserve recorded JSON evidence and provenance."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_reliability.reporting import write_suite_report  # noqa: E402

for name in ("showcase", "langgraph", "http-transport", "code-validation"):
    directory = ROOT / "examples" / name
    write_suite_report(
        directory,
        json.loads((directory / "results.json").read_text()),
        json.loads((directory / "manifest.json").read_text()),
    )
