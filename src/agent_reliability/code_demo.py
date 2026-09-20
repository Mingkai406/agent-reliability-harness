"""Executable patch-validation showcase using evaluator-owned regression checks."""

import argparse
import asyncio
import difflib
import json
import subprocess
import tempfile
from pathlib import Path

from .code_validation import CodeValidationAdapter
from .contracts import Case
from .suite import run_matrix

BASE = "def add(a: int, b: int) -> int:\n    return a - b\n"
FIXED = "def add(a: int, b: int) -> int:\n    return a + b\n"
UNIT = """import unittest
from src.calc import add

class TestAdd(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

if __name__ == "__main__":
    unittest.main()
"""
INTEGRATION = """import json
import subprocess
import sys
import unittest

class TestCLI(unittest.TestCase):
    def test_json_contract(self):
        result = subprocess.run([sys.executable, "-m", "src.cli", "8", "4"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"sum": 12})

if __name__ == "__main__":
    unittest.main()
"""


def diff(before, after, path="src/calc.py"):
    return "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile="a/" + path,
            tofile="b/" + path,
        )
    )


def prepare(root):
    files = {
        "src/__init__.py": "",
        "src/calc.py": BASE,
        "src/cli.py": "import json\nimport sys\nfrom .calc import add\n\n"
        'print(json.dumps({"sum": add(int(sys.argv[1]), int(sys.argv[2]))}))\n',
        "tests/__init__.py": "",
        "tests/test_unit.py": UNIT,
        "tests/test_integration.py": INTEGRATION,
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for args in (
        ["init", "--quiet"],
        ["add", "."],
        [
            "-c",
            "user.name=Harness",
            "-c",
            "user.email=harness@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Trusted evaluator fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    patches = {
        "valid": diff(BASE, FIXED),
        "syntax": diff(BASE, "def add(:\n    pass\n"),
        "type": diff(BASE, 'def add(a: int, b: int) -> int:\n    return "wrong"\n'),
        "unit": diff(BASE, "def add(a: int, b: int) -> int:\n    return 0\n"),
        "integration": diff(
            BASE, "def add(a: int, b: int) -> int:\n    return 0 if a == 8 else a + b\n"
        ),
        "static": diff(BASE, "import os\n\n" + FIXED),
        "timeout": diff(BASE, "def add(a: int, b: int) -> int:\n    while True:\n        pass\n"),
        "protected": diff(UNIT, "# checks removed\n", "tests/test_unit.py"),
    }
    checks = {
        "build": [
            "python",
            "-c",
            "import pathlib; "
            "[compile(p.read_text(),str(p),'exec') for p in pathlib.Path('src').glob('*.py')]",
        ],
        "types": ["mypy", "--cache-dir", "/tmp/mypy", "--strict", "src"],
        "unit": ["python", "-m", "unittest", "tests.test_unit"],
        "integration": ["python", "-m", "unittest", "tests.test_integration"],
        "static": ["ruff", "check", "--no-cache", "src"],
    }
    return CodeValidationAdapter(root, patches, checks, timeout=5), [
        Case(
            "code-" + key,
            "code-validation",
            {"candidate": key},
            expected="completed" if key == "valid" else "rejected",
        )
        for key in patches
    ]


async def run(output):
    with tempfile.TemporaryDirectory(prefix="harness-trusted-") as temp:
        adapter, cases = prepare(Path(temp))
        root, results = await run_matrix(output, cases, {"code-validation": adapter})
    # Verify the intended gate, not only an arbitrary rejection.
    expected_gate = {
        "syntax": "build",
        "type": "types",
        "unit": "unit",
        "integration": "integration",
        "static": "static",
        "timeout": "unit",
    }
    for key, gate in expected_gate.items():
        snapshot = json.loads((root / ("code-" + key) / "snapshot.json").read_text())
        assert not snapshot["gates"][gate]["passed"], (key, gate)
    assert all(r["scenario_passed"] for r in results)
    return root, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/code-validation"))
    args = parser.parse_args()
    root, results = asyncio.run(run(args.output))
    print(f"Report: {root / 'index.html'}; expected outcomes {len(results)}/{len(results)}")


if __name__ == "__main__":
    main()
