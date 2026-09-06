# Architecture and guarantees

The reusable runner is `suite.run_matrix`. A `Case` describes an application, validated
configuration, fault schedule, expected outcome and (for a negative control) expected failing
checks. The core, LangGraph and full profiles use the same runner, fault engine and report schema.

## Responsibilities

| Component | Owns | Does not own |
|---|---|---|
| Runner | Fresh case directories, manifests, deadline, outcome matching, reporting | Application retries or business permissions |
| Fault engine | Before/after injections, occurrence counts, durable schedule claims | Network interception, provider quotas or recovery |
| Adapter | Tool hooks, error translation, bounded execution, state export | A universal definition of business success |
| Application | Authorization, idempotency, transactions, checkpoints | The harness's verdict |
| State oracle | Business invariants over persisted state and receipts | Arbitrary semantic truth or hostile-process attestation |

All built-in matrix adapters execute offline. Refund and artifact drivers are deterministic
tool programs. CreatorPal uses a real ADK tool loop with a deterministic `BaseLlm` double.
LangGraph uses a real `StateGraph` with deterministic nodes, native retry policies and a
persistent SQLite checkpointer. See [checkpoint and business-state boundaries](langgraph.md).
The legacy refund `evaluate` runner is the separate live-inference entry point.

## Fault scheduling

A rule matches an exact tool name and phase. Matching boundary calls increment its durable
counter. `occurrence=3, repeat=2` injects on the third and fourth matching calls, even across
worker processes. One rule per tool/phase avoids ambiguous precedence.

SQLite `BEGIN IMMEDIATE` serializes the counter increment and injection event. Reopening a
journal with a different schedule fails. Coverage requires the entire repetition count: a
task that succeeds without reaching its scheduled fault is an unsuccessful experiment.
The journal records no tool arguments or model prompts.

The recorded injection and subsequent Python exception are not one atomic operation.
An arbitrary kill between them could leave a claimed event without a delivered exception.
Controlled interruption cases deliver the exception normally. The separate SIGKILL recovery
test pauses at a known application boundary with no fault rule, then kills the process.

## Artifact workflow

1. Retrieve a synthetic record set and persist it.
2. Build and persist a summary containing a numeric aggregate.
3. Commit under a stable operation key. The artifact and commit event share one transaction;
   a different payload under the key is rejected.
4. Materialize `summary.json` from committed content using a temporary file and atomic rename.
5. Persist a completion receipt only after a validated export response.

After commit but before export, a new worker reuses the committed artifact and materializes
the missing file. After export but before checkpoint, it checks existing bytes and writes the
checkpoint. The oracle checks the aggregate, source records, commit count, file, digest and
checkpoint. SQLite remains the source of truth. This is local reconciliation, not a distributed
transaction or general power-loss guarantee. Tests cover a real kill after database commit.

## Grading and evidence

`completed` and `rejected` require every check to pass. `violation` requires a failing check.
Negative controls declare exact failing check names; an unrelated failure cannot substitute.
Unhandled adapter errors use `observed=error` and always fail the experiment.

Each case retains `snapshot.json`, `result.json`, `faults.sqlite`, `traces.jsonl` and application
artifacts. CreatorPal also retains its own manifest/trace under `application/`. The suite
manifest records the full configuration and SHA-256, Python package-source fingerprint,
adapter source fingerprint, Python and installed package versions. These are reproduction
aids, not signed attestations or a fingerprint of every transitive/custom dependency.

The 120-second runner deadline is cooperative asyncio cancellation. Built-in tools have
bounded retries; CreatorPal also bounds model calls and analytics execution. Custom adapters
must bound blocking calls and use process isolation where needed. Registration loads trusted
local Python code; it does not create a security sandbox.

There is no distributed queue, multi-agent scheduler, live model-quality result or production
deployment in the current evidence. For a remote service, read its authoritative state and
verify its own idempotency/reconciliation contract in the oracle.
