# Testing and interpreting the evidence

Run from the repository root. The engineering checks below need no model credentials.

## Full checks

```sh
uv sync --locked --extra adk --extra langgraph --extra dev
uv pip install --python .venv/bin/python -r integration/creatorpal-requirements.txt
uv run --no-sync python -c 'import creatorpal_agent, langgraph.graph, langgraph.checkpoint.sqlite'
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -q
uv run --no-sync python -m build
uv run --no-sync agent-reliability run --profile full --output runs
```

The explicit imports prevent optional integration tests from silently skipping. Use `--no-sync`
after installing the separately pinned application, or reinstall it after a dependency sync.

The full profile has 42 cases: **31 completed tasks, 6 safe rejections and 5 detected negative
controls**. All 42 match expectations in the committed example. The LangGraph-only profile
has 10 cases: 7 completions, 2 safe rejections and 1 detected control. The core profile has 24 cases:
18 completions, 3 safe rejections and 3 detected controls.

## Regression coverage

- Refund concurrency, authorization, conflicting payloads and bounded retries.
- Real ADK tool loops with offline doubles, false completion and sanitized provider errors.
- CreatorPal recovery, citations, source requirements, analysis and atomic commit checks.
- Durable shared fault scheduling across concurrent calls and fresh engine instances.
- Invalid configuration, missed/partial schedules, exact negative controls and plugin loading.
- Real artifact files, independent numeric/content/receipt checks and corrupted outputs.
- Separate-process resume before/after commit and after export, with one committed result.
- LangGraph disk checkpoints, node retries, exact receipt controls and optional-dependency isolation.
- A real LangGraph child-process SIGKILL after a business commit but before its graph checkpoint.
- A real child-process SIGKILL between database commit and file export, then reconciliation.

Use `pytest -q tests/test_langgraph.py` for the LangGraph integration and
`pytest -q tests/test_framework.py` for the reusable framework. The original
`test_reliability.py`, `test_adk.py` and `test_creatorpal.py` retain compatibility coverage.
ADK dependency deprecation/experimental-feature warnings can appear; they do not replace checks.

## Inspect the evidence

Expand a case in the generated HTML report, then read:

1. `result.json`: expected/observed outcome, invariant checks, fault coverage and error type.
2. `snapshot.json`: exported final application state.
3. `faults.sqlite`: injection events and matching-call counters.
4. `traces.jsonl`: case/tool/fault spans. CreatorPal also saves its own trace and task/corpus/skill
   manifest in `application/`.
5. Suite `manifest.json`: full configuration and source fingerprints.

A passing case must match the declared outcome and cover the full fault schedule. Negative
controls must fail exactly the named checks. Generic exceptions do not substitute for state
violations. Safe rejection is separate from task completion.

## Container and wheel

```sh
docker build -t agent-reliability-harness .
docker run --rm -p 8080:8080 agent-reliability-harness
```

At `http://localhost:8080/`, the container serves a 24-case core report. It exposes no live
model-execution endpoint. CI checks HTTP access and uploads generated experiment directories.
Install the wheel in a fresh environment and run `agent-reliability run` to verify the core
package works independently, including its packaged HTML template.

## Existing commands and live models

| Command | Scope |
|---|---|
| `run` / `run --profile full` | Shared framework; core/full offline profiles |
| `run --profile langgraph` | Ten LangGraph controls, no ADK dependency or model credentials |
| `langgraph-worker` | One disk-checkpointed LangGraph invocation; interruption exits 75 |
| `run --suite FILE --plugin NAME=MODULE:FACTORY` | Explicit local integration |
| `serve` | Generate and serve a framework report |
| `artifact-worker` | One resumable invocation; controlled interruption exits 75 |
| `demo` / `serve-demo` | Original refund-only scripted report |
| `creatorpal` | Original CreatorPal offline fault report |
| `evaluate --model MODEL_ID` | Separate live ADK refund experiment |

The legacy refund report has baseline 4/7 and guarded 7/7. Its three baseline failures are
intentional. Legacy CreatorPal reports 8/8 expected scenarios with 6 completed tasks. The new
report explicitly separates its false-completion control from safe rejection. Its original
analytics-timeout injection also remains; the generic profile uses an explicit permanent
analytics error, with its own configuration and results.

Live model validation is deferred. After configuration, `evaluate --model "$FAST_MODEL"`
deliberately invokes a provider on the refund suite and can incur charges. CreatorPal's own
`creatorpal-agent compare --adapter adk` evaluates research quality and model/skill policies.
The shared built-in matrix does not yet offer live-model comparison. Use repeated trials and
report variability before claiming real success, cost or latency improvements.
