"""One runner for application adapters, durable faults, state oracles and reports."""

import asyncio
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import platform
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .adapters import BUILTINS
from .contracts import Case
from .faults import FaultEngine
from .reporting import write_suite_report
from .telemetry import provider_for


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def load_suite(path):
    data = json.loads(Path(path).read_text())
    if (
        set(data) != {"schema_version", "cases"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Suite requires schema_version=1 and cases")
    if not isinstance(data["cases"], list):
        raise ValueError("Suite cases must be a list")
    return [Case.from_dict(row) for row in data["cases"]]


def registry_with_plugins(specs=()):
    registry = {name: factory() for name, factory in BUILTINS.items()}
    for spec in specs:
        name, target = spec.split("=", 1)
        module, factory = target.split(":", 1)
        Case("validate-plugin-name", name)
        if name in registry:
            raise ValueError("Plugin name is already registered")
        # An explicit CLI flag loads trusted installed Python code. JSON never imports code.
        registry[name] = getattr(importlib.import_module(module), factory)()
    return registry


async def run_matrix(output, cases, registry=None):
    registry = registry if registry is not None else registry_with_plugins()
    if not cases or len(cases) > 500 or len({c.id for c in cases}) != len(cases):
        raise ValueError("Suite requires 1–500 uniquely named cases")
    for case in cases:
        if case.adapter not in registry:
            raise ValueError(f"Unknown adapter: {case.adapter}; register with --plugin")
        registry[case.adapter].validate(case)
        # Validate conflicting schedules before creating any experiment directories.
        if len({(r.tool, r.phase) for r in case.faults}) != len(case.faults):
            raise ValueError("Use one fault rule per tool/phase")
    configuration = {"schema_version": 1, "cases": [c.to_dict() for c in cases]}
    encoded = json.dumps(configuration, sort_keys=True, allow_nan=False)
    packages = {}
    for package in (
        "agent-reliability-harness",
        "creatorpal-agent",
        "google-adk",
        "langgraph",
        "langgraph-checkpoint-sqlite",
        "opentelemetry-sdk",
    ):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    adapter_sources = {}
    for name in sorted({c.adapter for c in cases}):
        cls = type(registry[name])
        source = inspect.getsourcefile(cls)
        adapter_sources[name] = {
            "class": f"{cls.__module__}.{cls.__qualname__}",
            "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()
            if source
            else None,
        }
    source_hash = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        source_hash.update(path.name.encode())
        source_hash.update(path.read_bytes())
    root = Path(output) / ("suite-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "configuration": configuration,
        "configuration_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "harness_source_sha256": source_hash.hexdigest(),
        "python": platform.python_version(),
        "packages": packages,
        "adapters": adapter_sources,
        "measurement": "Pending execution; consult per-case measurement",
        "execution": "Sequential cases, fresh state, adapter-owned bounded retries and recovery",
        "case_timeout_seconds": 120,
    }
    dump(root / "manifest.json", manifest)
    results = []
    for case in cases:
        directory = root / case.id
        directory.mkdir()
        provider = provider_for(directory / "traces.jsonl")
        tracer = provider.get_tracer("matrix")
        faults = FaultEngine(directory / "faults.sqlite", case.faults, tracer)
        started = time.perf_counter()
        row = {
            "id": case.id,
            "adapter": case.adapter,
            "expected": case.expected,
            "observed": "error",
            "scenario_passed": False,
            "task_completed": False,
            "checks": {},
            "receipt": None,
            "measurement": None,
            "error_type": None,
        }
        try:
            with tracer.start_as_current_span(
                "harness.case", record_exception=False, set_status_on_exception=False
            ) as span:
                span.set_attribute("harness.case", case.id)
                span.set_attribute("harness.application", case.adapter)
                adapter = registry[case.adapter]
                execution = await asyncio.wait_for(
                    adapter.execute(case, directory, faults, tracer), timeout=120
                )
                dump(directory / "snapshot.json", execution.snapshot)
                assessment = adapter.assess(case, execution)
                failed_checks = {key for key, value in assessment.checks.items() if not value}
                observed = (
                    "completed"
                    if assessment.completed
                    else "rejected"
                    if assessment.safely_rejected
                    else "violation"
                )
                row.update(
                    observed=observed,
                    task_completed=assessment.completed,
                    checks=assessment.checks,
                    receipt=execution.receipt,
                    measurement=execution.measurement,
                    scenario_passed=observed == case.expected
                    and faults.covered()
                    and failed_checks == set(case.expected_failed_checks),
                )
                span.set_attribute("harness.scenario_passed", row["scenario_passed"])
        except Exception as exc:
            # Errors never count as an expected negative control. Avoid persisting raw exceptions.
            row["error_type"] = type(exc).__name__
        finally:
            provider.shutdown()
        row.update(
            expected_failed_checks=list(case.expected_failed_checks),
            faults=[asdict(r) for r in case.faults],
            fault_events=faults.events(),
            fault_coverage=faults.covered(),
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            usage=None,
            cost_usd=None,
        )
        dump(directory / "result.json", row)
        results.append(row)
    measurements = sorted({r["measurement"] for r in results if r["measurement"]})
    offline = {
        "scripted tools",
        "ADK Runner + offline model double",
        "LangGraph + deterministic nodes; no model calls",
    }
    manifest["measurement"] = (
        "Deterministic offline controls; no live model inference"
        if measurements and set(measurements) <= offline
        else "Adapter-declared execution: " + "; ".join(measurements)
    )
    dump(root / "manifest.json", manifest)
    dump(root / "results.json", results)
    write_suite_report(root, results, manifest)
    return root, results
