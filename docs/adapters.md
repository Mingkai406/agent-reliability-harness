# Connect an application

Start with the executable [external key/value adapter](../examples/custom_adapter.py).
It imports the public contracts and fault exceptions, writes to an actual SQLite database,
and runs without changing the built-in adapter registry:

```sh
PYTHONPATH=examples agent-reliability run \
  --plugin kv=custom_adapter:create_adapter \
  --suite examples/suites/custom.json
```

## Three methods

**`validate(case)`** rejects unsupported configuration and unknown boundaries before any
experiment starts. Import required optional dependencies here so missing applications fail early.

**`async execute(case, directory, faults, tracer)`** invokes the application and returns an
`Execution(receipt, snapshot, measurement)`. Save state in the fresh case directory. Wrap the
real tool boundary instead of returning an invented success record:

```python
faults.apply("save", "before")
result = await real_tool_call()
result = faults.apply("save", "after", result)
return result
```

The boundary also works around synchronous calls. Translate `RetryableFault`, `PermanentFault`
and `InterruptedFault` into the application's own retry/error/restart types. Malformed-response
injection returns `{"harness_invalid_response": true}`. Let the application's validator handle
it. Do not silently repair every result in the harness: expose the actual recovery policy.
Bound calls and retries; cooperative asyncio cancellation cannot interrupt arbitrary blocking code.

**`assess(case, execution)`** returns `Assessment(completed, safely_rejected, checks)` using
persisted artifacts and independently defined business rules. Read authoritative records or
export a versioned snapshot. Do not import the application's success grader. Absence of side
effects alone is insufficient for safe rejection; require the expected denial/error evidence.

Integrations run trusted code. An external snapshot is not an adversarial security boundary.
For a remote service, read authoritative state through a read-only integration.

## Configuration contract

A file contains exactly `schema_version: 1` and a nonempty `cases` array:

| Field | Meaning |
|---|---|
| `id` | Unique lowercase letters/digits/hyphens/underscores, at most 80 characters |
| `adapter` | Built-in name or explicitly registered local plugin |
| `config` | Application-specific configuration validated by the adapter |
| `faults` | Zero or more rules, one rule per tool/phase |
| `expected` | `completed`, `rejected` or `violation` |
| `expected_failed_checks` | Required nonempty list only for `violation`; exact failing check names |

Rules contain `tool`, `phase`, `kind`, `occurrence` (default 1), and `repeat` (default 1).
Numeric fields accept integers 1–100. Before-tool kinds: `timeout`, `rate_limit`, `interrupt`,
`permanent_error`. After-tool kinds: `lost_ack`, `malformed`, `interrupt`.

JSON cannot import arbitrary modules: loading requires the explicit
`--plugin name=module:factory` flag. Built-in names cannot be overridden. The runner accepts
at most 500 cases and runs them sequentially with fresh state. This is not a scale benchmark.

## Test the oracle

Deliberately duplicate a write, forge a receipt, omit an artifact or reference an unknown
source. Specify exactly which checks must fail. Also schedule a fault that never fires: the
case must fail even when the application completes. A crash is an error, not a passing control.

For Python registration use `run_matrix(output, cases, registry={"myapp": adapter})`. Pin
application/fixture versions alongside the suite. Commit a representative report and retain
full state/traces as CI artifacts; a screenshot alone does not verify an outcome.
