# Testing the Agent Reliability Harness

The Harness contains two application suites. The refund suite checks its SQLite service directly; the CreatorPal suite injects external tool faults and grades versioned application snapshots. Run commands from the Harness repository root.

## 1. Install the pinned integration and run tests

```sh
uv sync --locked --extra adk --extra dev
uv pip install --python .venv/bin/python -r integration/creatorpal-requirements.txt
uv run --no-sync python -c 'import creatorpal_agent'
uv run --no-sync pytest -q
```

The expected full suite has 53 passing tests: 39 existing refund/ADK checks and 14 CreatorPal integration/grader checks. The explicit import ensures the optional application is installed. Without it, pytest can skip the CreatorPal module; a skipped module does not establish integration coverage. Use `--no-sync` after installing the separate pinned application, or reinstall it after rebuilding the environment.

## 2. Run the two fault experiments

```sh
uv run --no-sync agent-reliability demo --output runs/refund
uv run --no-sync agent-reliability creatorpal --output runs/creatorpal
```

Expected results:

| Suite | Expected outcome | What it establishes |
|---|---|---|
| Refund | Baseline 4/7, guarded 7/7 | The intentionally incomplete baseline fails ambiguous-outcome cases; combined controls prevent duplicate/invalid refunds in these fixtures |
| CreatorPal | 8/8 scenarios pass; 6 tasks complete | Recovery works in six cases and two expected failures are rejected without a report |

The baseline's three failed scenarios are intentional controls. The refund command exits successfully when the guarded cases pass. CreatorPal's analytics-timeout and false-completion cases should **not** become completed research tasks. Counting all eight as successful task completions would be an evaluation error.

Both commands print the report path. For CreatorPal, inspect `report.md`, `suite-results.json` and `suite-manifest.json`, then inspect a case's `state-snapshot.json`, `result.json` and `traces.jsonl`. For refunds, open `index.html` and inspect the per-case SQLite state and trace files. All fixtures are synthetic.

## 3. Inspect recovery and negative controls

```sh
uv run --no-sync pytest -q tests/test_creatorpal.py
```

The suite should reject corrupted report counts, task IDs, cross-community citations, absent rules, absent analysis, duplicate commit events and forged receipts. The tests change the artifact and, where appropriate, recompute its receipt digest, so a stale hash alone is not the only rejection mechanism.

Look for the scheduled `harness_fault` event and a valid final state. A scenario should not pass merely because no exception escaped. For a lost report acknowledgment, the result must remain one report with a matching receipt. For an execution failure, the expected error must actually have been observed and no report may be committed.

CreatorPal also tests a real process exiting immediately after its report-and-audit transaction, then restarting against that database. To reproduce that specific test, use the [CreatorPal test instructions](https://github.com/Mingkai406/CreatorPal/blob/main/doc/agent/testing.md) and select `test_restart_after_process_dies_after_commit` from `tests_agent/test_research.py`. Harness interruption hooks simulate an interruption at a specific tool boundary; they are not claims about a distributed worker outage.

## 4. Verify the container demo

With Docker running:

```sh
docker build -t agent-reliability-harness .
docker run --rm -p 8080:8080 agent-reliability-harness
```

Open `http://localhost:8080/` to view the synthetic refund report. Stop the foreground container when finished. GitHub CI performs this smoke test and requires a successful HTTP response. The container serves the original refund demo; it does not expose a public CreatorPal agent-execution endpoint. CreatorPal's separate analytics container is tested in that repository.

## 5. Test real models after configuration

These commands have different scopes:

| Command | Model behavior |
|---|---|
| `agent-reliability demo` | Scripted refund control; no model calls |
| `agent-reliability creatorpal` | Real ADK Runner with deterministic doubles; no live inference |
| `agent-reliability evaluate --model MODEL_ID` | Live model on the refund fault suite |
| CreatorPal's `creatorpal-agent compare --adapter adk ...` | Live research-task quality and policy comparison |

The CreatorPal fault CLI currently accepts `scripted` or `offline-adk`; it does not accept a live model ID. A live CreatorPal fault benchmark would require extending that adapter and defining how nondeterministic model failures are scored. Do not label the current 8/8 control as a live-model fault benchmark.

Once credentials and an accessible Gemini model are configured in your shell, the existing refund live suite can be run deliberately:

```sh
uv run --no-sync agent-reliability evaluate --model "$FAST_MODEL" --output runs/refund-live
```

This invokes a provider for 14 cases (seven scenarios times two modes), allows multiple calls per task and may incur charges. Do not expect the live numbers to exactly equal the scripted control; record the actual model, failures, configuration and usage. A passing control does not predict a production reliability rate.

For CreatorPal, first run one live task, then a small policy pilot, then a frozen real-data evaluation with repeated trials. The [CreatorPal testing guide](https://github.com/Mingkai406/CreatorPal/blob/main/doc/agent/testing.md) includes commands, metric interpretation and a human-review rubric.

## 6. Reproduce a future failure

Keep the Git commits for both repositories, the pinned integration requirement, scenario manifests and all result artifacts. Describe the fault schedule and expected outcome before rerunning. If the state contract changes, update the external grader and its negative controls together, repin the tested application commit and run the complete CI suite. When only the model changes, preserve task/corpus/skill versions and use fresh task state.

GitHub CI is credential-free. Its uploaded `synthetic-experiment-report` artifact contains both suites' offline results, making later regressions inspectable without exposing provider secrets or private research data.
