import argparse
import asyncio
import functools
import http.server
import json
import os
from dataclasses import asdict
from pathlib import Path

from .experiment import run_suite
from .runtime import Gateway, InterruptedRun, run_scripted
from .scenarios import SCENARIOS, scenario_by_name
from .store import Store
from .telemetry import provider_for


def worker(args):
    scenario = scenario_by_name(args.scenario)
    store = Store(args.db)
    binding = {"scenario": asdict(scenario), "mode": args.mode}
    prior = store.get("worker_config")
    if prior is not None and prior != binding:
        raise ValueError("This database belongs to a different scenario or mode")
    store.set("worker_config", binding)
    provider = provider_for(args.db.with_suffix(".traces.jsonl"))
    try:
        result = run_scripted(Gateway(store, scenario, args.mode, provider.get_tracer("worker")))
        print(json.dumps(result))
        return 0 if result.get("status") in {"refunded", "denied"} else 1
    except InterruptedRun:
        store.event("worker_interrupted")
        print(json.dumps({"status": "interrupted", "resume": "Run the same command again"}))
        return 75
    finally:
        provider.shutdown()


def main():
    parser = argparse.ArgumentParser(description="State-based agent fault-injection experiments")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "evaluate"):
        sub = commands.add_parser(name)
        sub.add_argument("--output", type=Path, default=Path("runs"))
        if name == "evaluate":
            sub.add_argument("--model", required=True, help="Explicit ADK/Gemini model identifier")
    app = commands.add_parser("creatorpal", help="Inject faults into CreatorPal's actual tools")
    app.add_argument("--output", type=Path, default=Path("runs"))
    app.add_argument("--adapter", choices=["scripted", "offline-adk"], default="offline-adk")
    process = commands.add_parser("worker", help="One resumable synthetic worker invocation")
    process.add_argument("--db", type=Path, required=True)
    process.add_argument("--scenario", choices=[s.name for s in SCENARIOS], required=True)
    process.add_argument("--mode", choices=["baseline", "guarded"], default="guarded")
    server = commands.add_parser("serve-demo", help="Serve a freshly generated synthetic report")
    server.add_argument("--output", type=Path, default=Path("/tmp/harness-demo"))
    server.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()
    if args.command == "creatorpal":
        try:
            import creatorpal_agent  # noqa: F401

            from .creatorpal import run_creatorpal_suite
        except ImportError:
            parser.error("Install CreatorPal's agent package and the ADK extra; see README")
        root, results = asyncio.run(run_creatorpal_suite(args.output, adapter=args.adapter))
        print(f"Report: {root / 'report.md'}")
        print(f"Scenarios passed: {sum(r['scenario_passed'] for r in results)}/{len(results)}")
        return 0 if all(r["scenario_passed"] for r in results) else 1
    if args.command == "worker":
        return worker(args)
    adapter = "adk" if args.command == "evaluate" else "scripted"
    if adapter == "adk":
        try:
            import google.adk  # noqa: F401
        except ImportError:
            parser.error('Install the ADK extra: pip install -e ".[adk]"')
        if not (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
        ):
            parser.error("Configure GOOGLE_API_KEY or explicit Vertex AI authentication first")
    root, results = asyncio.run(run_suite(args.output, adapter, getattr(args, "model", None)))
    print(f"Report: {root / 'index.html'}")
    for mode in ("baseline", "guarded"):
        selected = [r for r in results if r["mode"] == mode]
        print(f"{mode}: {sum(r['passed'] for r in selected)}/{len(selected)} passed ({adapter})")
    if args.command == "serve-demo":
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        with http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
            print(f"Serving synthetic report on port {args.port}", flush=True)
            httpd.serve_forever()
    return 0 if all(r["passed"] for r in results if r["mode"] == "guarded") else 1
