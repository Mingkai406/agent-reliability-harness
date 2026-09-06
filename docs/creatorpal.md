# CreatorPal reliability adapter

For the reusable framework, run `agent-reliability run --profile full` after installation.
It uses the shared fault engine and unified HTML report across four integrations. The
CreatorPal cases produce six completed tasks, one safe rejection and one detected
false-completion control. See [adapter authoring](adapters.md) for the actual plugin contract.
The original `creatorpal` command and its application-specific report are documented below.

This suite applies external faults to the real CreatorPal research tools. It uses a versioned artifact snapshot and its own grader, never importing CreatorPal's `completed` or `score` function. This makes the Harness useful for another application, beyond the original synthetic refund service.

See the [testing guide](testing.md) for the complete regression sequence, expected results, container checks and live-model boundaries.

## Install and run

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[adk,dev]'
pip install -r integration/creatorpal-requirements.txt
agent-reliability creatorpal --output runs
pytest -q
```

The application dependency is pinned to a Git commit in `integration/creatorpal-requirements.txt`. It is optional; the refund experiments and core wheel work without CreatorPal. CI explicitly installs and imports the pinned application before running the full test suite so the integration tests cannot silently skip.

The default `offline-adk` adapter uses the actual ADK Runner with deterministic model doubles. `--adapter scripted` uses a fixed control program for seven cases; its false-completion case still crosses the real ADK model interface with a deliberately dishonest model double. No provider credentials or live model calls are needed. Live model policy experiments are exposed by CreatorPal's separate `compare --adapter adk` command after environment configuration.

## Faults and expected outcomes

| Fault | Injection boundary | Required outcome |
|---|---|---|
| Clean | None | One valid committed report |
| Retrieval timeout | Before search, once | Bounded retry, then one report |
| Malformed retrieval | After search, once | Reject malformed tool result, retry cached valid result |
| Analytics timeout | Before every analytics execution | Failure recorded; no report submitted |
| Interruption before publish | Before commit, once | Restore artifacts, then one report |
| Lost report acknowledgment | After commit, once | Repeat submission reuses the original report |
| Interruption after publish | After commit, once | Resume using the persisted report and receipt |
| False completion | Model final answer without tool work | Claimed success rejected; no report |

The analytics timeout is an injected execution error at the tool boundary, not a claim that this suite kills an actual remote worker. CreatorPal separately tests its real child-process wall deadline and a process that exits after committing its report. The interruption cases here use a fault exception and a fresh ADK session over persisted artifacts.

Eight passing scenarios mean six tasks completed and two expected failures were correctly rejected. They do **not** mean 100% model reliability. The current fixtures are synthetic Python-community research tasks, not a public benchmark or a sampled workload.

## Grading contract

`state-snapshot.json`, contract version 1, exposes:

- Task requirements and ID.
- Retrieved evidence with IDs, source/community relationships and kinds.
- Persisted analytics records, including program, input rows and result.
- Committed reports and ordered audit events.

The independent grader checks exactly one report, task scope, recommendation limits, unique communities, required profile/rules citations, referenced analysis inputs, one commit event, and a completion receipt whose digest matches the report. The suite additionally checks that its designated fault was observed. Negative-control tests corrupt citations, reports, analysis records and receipts; a producer-supplied `completed: true` is never trusted.

These are structural invariants. Numeric correctness and semantic grounding require separate evaluation; the grader cannot prove that a cited document supports arbitrary report prose. The artifact snapshot is exported by a trusted application under test, so this is not a hostile-process attestation, cryptographic audit, or security certification.

Each run emits a scenario table, machine-readable results, per-case application configuration hashes and state artifacts. Custom tool spans are local OpenTelemetry JSONL records. The checked-in example is labeled as an offline synthetic control.

## Add another application

Implement a narrow fault hook at that application's tool boundary and export a documented final-state artifact. Write an application-specific grader outside its success function. Specify which faults must recover and which must fail without committing a result, add deliberately bad outputs to test the grader, and preserve immutable fixture/configuration hashes. Do not call every caught exception a successful reliability test.

The existing refund suite grades its SQLite service directly. CreatorPal's adapter grades its versioned snapshot. The common design is fault injection plus independently checked final state; the projects do not share a universal claim that every external side effect is exactly-once.
