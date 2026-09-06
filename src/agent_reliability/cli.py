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
    for name in ("run", "serve"):
        sub = commands.add_parser(name, help="Run a reusable application reliability suite")
        selection = sub.add_mutually_exclusive_group()
        selection.add_argument("--profile", choices=["core", "full", "langgraph"], default="core")
        selection.add_argument("--suite", type=Path, help="Versioned JSON suite configuration")
        sub.add_argument(
            "--plugin",
            action="append",
            default=[],
            help="Trusted installed adapter: name=module:factory",
        )
        sub.add_argument(
            "--output",
            type=Path,
            default=Path("/tmp/harness-demo") if name == "serve" else Path("runs"),
        )
        if name == "serve":
            sub.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    artifact_worker = commands.add_parser(
        "artifact-worker", help="One resumable artifact worker invocation"
    )
    artifact_worker.add_argument("--directory", type=Path, required=True)
    artifact_worker.add_argument("--case-id", default="artifact-restart-after-commit")
    graph_worker = commands.add_parser(
        "langgraph-worker", help="One resumable LangGraph invocation"
    )
    graph_worker.add_argument("--directory", type=Path, required=True)
    graph_worker.add_argument("--case-id", default="langgraph-restart-after-commit")
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
    if args.command in {"run", "serve"}:
        from .catalog import builtin_suite
        from .suite import load_suite, registry_with_plugins, run_matrix

        try:
            cases = load_suite(args.suite) if args.suite else builtin_suite(args.profile)
            registry = registry_with_plugins(args.plugin)
            root, results = asyncio.run(run_matrix(args.output, cases, registry))
        except (ValueError, TypeError, KeyError, ImportError, AttributeError, OSError) as exc:
            parser.error(str(exc))
        passed = sum(r["scenario_passed"] for r in results)
        print(f"Report: {root / 'index.html'}", flush=True)
        print(
            f"Expected outcomes: {passed}/{len(results)}; "
            f"completed tasks: {sum(r['task_completed'] for r in results)}",
            flush=True,
        )
        if args.command == "serve":
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
            with http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
                print(f"Serving offline report on port {args.port}", flush=True)
                httpd.serve_forever()
        return 0 if passed == len(results) else 1
    if args.command == "langgraph-worker":
        from .catalog import langgraph_suite
        from .faults import FaultEngine, InterruptedFault
        from .langgraph_adapter import LangGraphAdapter, run_graph_once

        case = next((c for c in langgraph_suite() if c.id == args.case_id), None)
        if case is None:
            parser.error("Unknown LangGraph case ID; see examples/suites/langgraph.json")
        adapter = LangGraphAdapter()
        try:
            adapter.validate(case)
        except ValueError as exc:
            parser.error(str(exc))
        args.directory.mkdir(parents=True, exist_ok=True)
        provider = provider_for(args.directory / "traces.jsonl")
        try:
            tracer = provider.get_tracer("langgraph-worker")
            faults = FaultEngine(args.directory / "faults.sqlite", case.faults, tracer)
            execution = run_graph_once(case, args.directory, faults, tracer)
            print(json.dumps(execution.receipt))
            assessment = adapter.assess(case, execution)
            return 0 if assessment.completed and faults.covered() else 1
        except InterruptedFault:
            print(json.dumps({"status": "interrupted", "resume": "Run the same command again"}))
            return 75
        finally:
            provider.shutdown()
    if args.command == "artifact-worker":
        from .artifacts import ArtifactStore, assess_artifacts, run_workflow
        from .catalog import builtin_suite
        from .faults import FaultEngine, InterruptedFault

        case = next(
            (c for c in builtin_suite() if c.adapter == "artifact" and c.id == args.case_id), None
        )
        if case is None:
            parser.error("Unknown artifact case ID; see examples/suites/core.json")
        store = ArtifactStore(args.directory)
        store.bind(case.to_dict())
        provider = provider_for(args.directory / "traces.jsonl")
        try:
            tracer = provider.get_tracer("artifact-worker")
            faults = FaultEngine(args.directory / "faults.sqlite", case.faults, tracer)
            receipt = run_workflow(store, faults, tracer)
            print(json.dumps(receipt))
            assessment = assess_artifacts(store.snapshot(), receipt)
            return 0 if assessment.completed and faults.covered() else 1
        except InterruptedFault:
            print(json.dumps({"status": "interrupted", "resume": "Run the same command again"}))
            return 75
        finally:
            provider.shutdown()
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
