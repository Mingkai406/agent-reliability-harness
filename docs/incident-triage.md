# Evidence-based incident triage

A successful HTTP response can hide duplicate database writes. The new triage command
reads **client receipts, an independently collected ledger snapshot, and correlated service
transaction events**, then produces a diagnosis, evidence references, and a suggested next check.
It never changes application data or applies a fix.

```sh
uv run agent-reliability run --profile http --output runs
# Replace suite-... with the directory printed by the run command.
uv run agent-reliability triage --run runs/suite-... --output runs/triage-new
uv run python -m agent_reliability.triage_eval --output runs/triage-eval-new
```

Output directories must be new. `triage.json` contains each evidence bundle and both
methods' answers; `report.md` is the short human-readable view. `E001`, etc. identify
observations whose `source` points to the original file/JSON field or JSONL line.

## Diagnoses and what supports them

| Diagnosis | Evidence required |
|---|---|
| Duplicate effect | Multiple durable entries with the same idempotency key |
| Conflicting payload rejected | HTTP 409, a changed requested amount, and a service transaction that reused rather than inserted an entry |
| Acknowledgement lost, recovered | A committed insert, a response-not-sent event correlated by attempt ID, and a retry returning that same durable entry |
| Clean observations | Successful receipts and service transactions matching one ledger entry per key |
| Insufficient evidence | Missing facts, unresolved outcomes, or contradictory receipts/service state |

A timeout alone does **not** establish that a write committed. A 409 alone does **not**
establish a payload conflict. `clean` describes these observations, not a proof that an
application has no bugs. The diagnosis taxonomy is intentionally restricted to the
HTTP charge adapter; other adapters retain their existing independent state checks.

Service events are recorded after the database transaction and before the response.
They include an attempt ID, key, entry ID, requested/stored amount, insertion flag and
HTTP status. The response-not-sent observation records the server closing that attempt
without sending response headers. The diagnostic bundle excludes case names, guarded/baseline
mode, fault schedules, expected outcomes, oracle checks, and harness trace labels. The grader
receives labels separately. Renaming or changing oracle labels does not alter diagnosis input.

## Measured local comparison

[Committed evaluation](../examples/triage/evaluation.json): eight real loopback HTTP/SQLite
experiments plus four explicitly modified evidence bundles (missing ledger, missing logs,
contradictory receipt, missing response-loss observation).

| Method | Correct labels | Duplicate effects found | False duplicate alarms on healthy cases | Correct abstentions on incomplete cases |
|---|---:|---:|---:|---:|
| Receipt-only rule (HTTP 200 means clean) | 3/12 | 0/3 | 0/5 | 0/4 |
| Evidence rules | 12/12 | 3/3 | 0/5 | 4/4 |
| Live model with citation gate | Not measured | — | — | — |

These are **development fixtures**, not held-out incidents. The receipt-only comparator
is deliberately simple and shows the limits of HTTP success checks; it is not a competitive
incident-diagnosis system. No model-accuracy, production-accuracy, or human MTTR improvement
is inferred from this table. Raw run artifacts are retained under `examples/triage/raw`;
the independent SQLite snapshots are included as JSON rather than binary databases.

## Optional model-assisted diagnosis

An OpenAI-compatible chat-completions endpoint can propose a label and evidence IDs.
This mode is opt-in; ordinary triage and CI make no remote model calls.

```sh
# For a remote HTTPS endpoint, set TRIAGE_API_KEY in your environment if required.
uv run agent-reliability triage --run runs/suite-... --output runs/model-triage-new \
  --model-url http://127.0.0.1:1234/v1/chat/completions --model YOUR_MODEL
uv run python -m agent_reliability.triage_eval --output runs/model-eval-new \
  --model-url http://127.0.0.1:1234/v1/chat/completions --model YOUR_MODEL
```

Only the whitelisted evidence bundle goes to the endpoint; HTTP redirects are refused. It can contain request keys
and amounts, so use sanitized experiment data when selecting an external provider.
The model receives no grader labels. Malformed outputs, unknown citations, unsupported
causes, missing proof, and transport errors become an explicit abstention. The returned
label must agree with the evidence rules and cite every observation used in their proof.
This is a **rules-gated assistant**, not an independent unrestricted root-cause model;
its accuracy cannot exceed the gate's supported diagnosis space. Proposals and provider
usage (when supplied) are retained for audit; the displayed explanation is evidence-derived.
The adapter has mocked transport/schema tests; live-model quality remains unmeasured.
