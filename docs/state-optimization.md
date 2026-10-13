# State layout and recovery tracing

The LangGraph reference application now supports two explicit layouts:

- `inline` (unchanged default): retrieved records are carried in graph state.
- `compact`: records are stored in the existing durable application store, while
  graph state carries no duplicate record list. The build node loads and validates
  the persisted source records. Checkpoints, output and idempotent commit semantics remain.

Select compact state on a new run directory; the bound case configuration prevents
mixing layouts in an existing directory. The independent business-state oracle
continues to verify records, aggregate, commit count, exported bytes and receipt.
This does not compact arbitrary agent memory or implement a generic context optimizer.

```sh
python -m agent_reliability langgraph-worker \
  --directory /tmp/compact-worker-new --case-id langgraph-restart-after-commit \
  --state-layout compact
# First call intentionally exits 75; repeat the same command to resume.
python -m agent_reliability.state_benchmark --output /tmp/state-comparison-new --trials 10
```

## Trace design

- `workflow.invoke`: one invocation, case, layout, resumed flag and pending nodes.
- `checkpoint.read`, `checkpoint.write`, `checkpoint.pending_writes`: actual saver I/O;
  checkpoint/task IDs, serialized size and pending-write count, without payload content.
- `tool.*`: per-attempt spans with tool name and attempt number, containing fault spans.
- `state.finish`: durable final application state write.
- A small atomically replaced context file links a subsequent process invocation to
  the previous invocation through an OpenTelemetry span link. It does not pretend a
  killed process exported a completed span. Context metadata is separate from state.

One worker per case directory is the execution contract. This is not a concurrent
multi-worker lease protocol. Cross-process links require surviving context metadata;
malformed metadata is ignored so it cannot invalidate business recovery.

## Benchmark interpretation

The benchmark alternates layout order across paired fresh-process trials. It reports
median/p95 recovery wall time (including process startup), serialized checkpoint and
pending-write payload sizes, allocated SQLite file size, replay counts and invariant
checks. It preserves raw trial rows and traces. It never assumes a latency gain.

The committed comparison uses a small deterministic two-record workflow. Compact
state reduces duplicate serialized data; the SQLite allocation may remain identical,
and an extra source-store read can outweigh the small serialization saving. Larger
workloads and live agents require separate measurement. No token/cost improvement is claimed.

Rollback: use the default inline layout with a new run directory. Existing inline
runs preserve their configuration binding and resume semantics.

## Validation for this branch

- Full local regression: 102 passed, 4 optional-dependency tests skipped.
- Both layouts: separate-process interruption/resume, completed replay as a no-op,
  and real SIGKILL after commit before graph checkpoint are tested.
- Compact layout: all 10 LangGraph scenario outcomes match expectations.
- Ruff checks pass.
- [Ten-pair measured comparison](../examples/state-optimization/comparison.md) and
  [raw trial measurements](../examples/state-optimization/comparison.json) are included.
- These are offline controls, not live-model or production performance measurements.
