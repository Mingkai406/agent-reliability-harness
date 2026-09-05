import asyncio
import hashlib
import importlib.metadata
import json
import platform
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .runtime import Gateway, InterruptedRun, run_scripted
from .scenarios import SCENARIOS
from .store import Store
from .telemetry import provider_for


def grade(scenario, store, response):
    refunds = store.snapshot()
    events = store.events()
    allowed = [r for r in refunds if r["order_id"] == scenario.order]
    checks = {
        "tenant_isolation": all(r["order_id"] == "order-a" for r in refunds),
        "no_duplicate_side_effect": len(allowed) <= 1,
        "expected_final_state": (
            len(refunds) == 0
            if scenario.expected == "denied"
            else len(refunds) == 1
            and refunds[0]["order_id"] == scenario.order
            and refunds[0]["amount"] == scenario.amount_cents
        ),
        "valid_completion": (
            response.get("status") == "denied"
            if scenario.expected == "denied"
            else Gateway._valid(response, scenario.order, scenario.amount_cents)
            and any(r["id"] == response.get("refund_id") for r in refunds)
        ),
        "fault_triggered": scenario.fault == "none"
        or any(e["kind"] == "fault_injected" for e in events),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "refund_count": len(refunds),
        "refund_total_cents": sum(r["amount"] for r in refunds),
        "tool_attempts": sum(e["kind"] == "tool_attempt" for e in events),
        "retryable_failures": sum(e["kind"] == "retryable_failure" for e in events),
    }


async def run_case(root: Path, scenario, mode, adapter="scripted", model=None):
    case_dir = root / f"{scenario.name}--{mode}"
    case_dir.mkdir(parents=True, exist_ok=False)
    store = Store(case_dir / "state.sqlite")
    provider = provider_for(case_dir / "traces.jsonl")
    tracer = provider.get_tracer("harness")
    started = time.perf_counter()
    usage = None
    had_interruption = False
    response = {"status": "failed"}
    try:
        with tracer.start_as_current_span("case.run") as span:
            span.set_attribute("harness.scenario", scenario.name)
            span.set_attribute("harness.adapter", adapter)
            span.set_attribute("harness.mode", mode)
            for restart in range(2):
                gateway = Gateway(store, scenario, mode, tracer)
                try:
                    if adapter == "scripted":
                        response = run_scripted(gateway)
                    else:
                        from .adk_adapter import run_adk

                        response, usage = await run_adk(gateway, model)
                    break
                except InterruptedRun:
                    had_interruption = True
                    # Creates a new client over the same persisted business state.
                    # Real subprocess restart behavior is covered separately by tests.
                    store.event("worker_interrupted", restart=restart)
                except Exception as exc:
                    # Preserve failed cases without writing provider errors or credentials to disk.
                    store.event("adapter_error", error_type=type(exc).__name__)
                    response = {"status": "failed", "error": type(exc).__name__}
                    break
            span.set_attribute("harness.passed", grade(scenario, store, response)["passed"])
    finally:
        provider.shutdown()
    result = {
        "scenario": scenario.name,
        "mode": mode,
        "adapter": adapter,
        "model": model,
        **grade(scenario, store, response),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "usage": None if had_interruption else usage,
        "usage_note": "Unavailable across interrupted invocations" if had_interruption else None,
        "cost_usd": None,
        "events": store.events(),
    }
    (case_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


async def run_suite(output: Path, adapter="scripted", model=None):
    root = output / ("run-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    results = []
    for scenario in SCENARIOS:
        for mode in ("baseline", "guarded"):
            results.append(await run_case(root, scenario, mode, adapter, model))
    config = {
        "scenarios": [asdict(s) for s in SCENARIOS],
        "adapter": adapter,
        "model": model,
        "max_tool_attempts": 3,
        "max_restarts": 1,
        "max_llm_calls_per_invocation": 8,
        "adk_timeout_seconds": 60,
    }
    versions = {}
    for package in ("agent-reliability-harness", "google-adk", "opentelemetry-sdk"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    manifest = {
        "config": config,
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "python": platform.python_version(),
        "packages": versions,
        "measurement": "synthetic scripted controls" if adapter == "scripted" else "live ADK agent",
        "limitations": "Seven synthetic scenarios; no production claims. Cost unmeasured.",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    write_report(root, results, manifest)
    return root, results


def write_report(root, results, manifest):
    rows = [
        "# Agent Reliability Harness",
        "",
        manifest["measurement"],
        "",
        "State is the oracle: success requires the right refund, no duplicate side effect, "
        "tenant isolation, and a valid receipt. All amounts are synthetic.",
        "",
        "| Scenario | Mode | Pass | Refunds | Tool attempts |",
        "|---|---|---|---:|---:|",
    ]
    for r in results:
        rows.append(
            f"| {r['scenario']} | {r['mode']} | {r['passed']} | "
            f"{r['refund_count']} | {r['tool_attempts']} |"
        )
    rows += [
        "",
        "Scripted results test harness mechanisms, not LLM capability. "
        "Duration is local end-to-end runtime, not provider inference latency. "
        "No measured model cost is available.",
    ]
    (root / "report.md").write_text("\n".join(rows) + "\n")
    # Self-contained static viewer. It only reads this generated synthetic report.
    import html

    table = "".join(
        f"<tr><td>{html.escape(r['scenario'])}</td><td>{r['mode']}</td>"
        f"<td class={'pass' if r['passed'] else 'fail'}>{'PASS' if r['passed'] else 'FAIL'}</td>"
        f"<td>{r['refund_count']}</td><td>{r['tool_attempts']}</td></tr>"
        for r in results
    )
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Agent Reliability Harness</title><style>"
        "body{font:16px system-ui;background:#101b29;color:#e7eef8;max-width:1000px;"
        "margin:60px auto;padding:24px}h1{font-size:38px}p{color:#b8c8dc;line-height:1.6}"
        "table{border-collapse:collapse;width:100%;font-size:14px}"
        "td,th{padding:13px;text-align:left;border-bottom:1px solid #304258}"
        ".pass{color:#6ee7b7}.fail{color:#fda4af}a{color:#93c5fd}</style>"
        "<p>FAULT INJECTION / STATE VERIFICATION</p><h1>Did the refund happen twice?</h1>"
        "<p>Seven synthetic scenarios. A retry-only baseline versus validated, idempotent "
        "tool execution. Database state determines the outcome.</p>"
        f"<p>Adapter: {html.escape(manifest['measurement'])}</p>"
        '<div style="overflow-x:auto"><table><tr><th>Scenario</th><th>Mode</th>'
        "<th>Outcome</th><th>Refunds</th><th>Attempts</th></tr>" + table + "</table></div>"
        "<p>Scripted runs measure control behavior, "
        "not model intelligence or production reliability. "
        "Cloud deployment and live-model benchmarks are separate validation steps.</p>"
        '<p><a href="results.json">Results JSON</a> · <a href="manifest.json">Run manifest</a></p>'
        "</html>"
    )


def demo(output):
    return asyncio.run(run_suite(Path(output)))
