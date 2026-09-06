# LangGraph: checkpoints are not proof of a committed result

The adapter executes a real LangGraph `StateGraph` with a persistent `SqliteSaver`.
It exercises the same retrieve/build/commit/export business contract as the plain artifact
workflow, but replaces its manual orchestration with LangGraph nodes and checkpoints.
The deterministic nodes make failures repeatable; there are **no model calls or model-quality
measurements** in this integration.

## Run it

```sh
pip install -e '.[langgraph]'
agent-reliability run --profile langgraph
```

Open the printed HTML report or read `results.json`. A selected integration without its
optional dependencies fails before creating any case directories. The default `core` profile
continues to work without LangGraph or ADK. `full` now requires both extras and CreatorPal.

## Execution and recovery

```text
retrieve -> build -> commit -> export -> finish
            graph state: checkpoints.sqlite
            business state: state.sqlite + summary.json
            fault schedule: faults.sqlite
```

- Each node runs through the shared before/after fault boundaries and emits a tool span.
- Native `RetryPolicy` retries only `RetryableFault`, at most three attempts per node invocation.
  Malformed results become retryable failures. Permanent errors and interruptions are not
  silently retried by this policy.
- `durability="sync"` persists graph checkpoints before subsequent steps. It does not make a
  business database commit atomic with a graph checkpoint.
- The adapter permits at most one controlled restart. Each restart opens a fresh graph and
  checkpointer using the same thread ID and calls `invoke(None)` on pending work. Completed
  node outputs come from checkpoints, not from rerunning the entire workflow.
- A stable artifact key makes a replayed commit idempotent; different content under that key
  is rejected. File export reconciles bytes from the committed database content.
- A completed invocation reuses its graph receipt. Reusing a directory with a different case
  or fault schedule is rejected. Terminal rejection is retained rather than resetting its
  retry budget on the next CLI invocation.

The databases are intentionally separate. This tests the gap between a committed side effect
and its recorded graph progress. It does not claim a distributed transaction or exactly-once
execution for arbitrary remote tools. Custom remote integrations need their own idempotency
or reconciliation contract. The synchronous graph runs in a worker thread; the harness's
async deadline does not forcibly kill a blocking thread. Nodes must remain bounded.

## Ten deterministic scenarios

| Scenario | Expected outcome |
|---|---|
| Clean | Complete |
| Retrieval timeout | Retry then complete |
| Malformed build result | Validate, retry then complete |
| Lost commit acknowledgment | Replay commit without duplicate writes |
| Interrupt before commit | Resume pending commit from checkpoint |
| Interrupt after commit | Replay committed node safely |
| Interrupt after export | Reconcile export then complete |
| Retrieval retry exhaustion | Reject without side effects |
| Permanent retrieval failure | Reject without side effects |
| Invalid completion receipt | Detect exactly the receipt/checkpoint mismatches |

The oracle reuses the independent artifact checks: source records, numeric aggregate, one
commit event, file bytes and receipt. It also requires a persisted graph checkpoint and, for
completion, an empty pending-node list and a graph receipt matching the returned receipt.
A graph reaching `END` does not override a failed business invariant. Rejection after a side
effect is not a safe rejection under this application's contract.

## Separate-process demonstration

Run the following command twice in the same directory:

```sh
agent-reliability langgraph-worker --directory runs/graph-recovery
```

The first invocation exits 75 after its database commit. The second process resumes and exits
0 after verifying the artifact and file. A third invocation returns the same receipt without
repeating tools. Use a new directory to start a new experiment.

```sh
pytest -q tests/test_langgraph.py
```

Tests additionally pause a worker after its business commit but before its node returns,
kill it with SIGKILL, and restart a fresh process. They check that retrieval ran once, the
commit node ran twice, but the commit event occurred once. Other tests tamper with file bytes
and graph state, exhaust retries after a committed side effect, and schedule an unreachable
fault to ensure the harness cannot report false success.

## Extend to your own agent

Keep your model node, graph routing and persistent checkpointer. Instrument real tool calls
with `faults.apply`, translate retryable errors deliberately, and implement an oracle against
your application's authoritative state. The reference adapter is one application integration,
not automatic instrumentation of every LangGraph application. See [adapter contracts](adapters.md).

References: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
and [persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
