# Commit succeeded, acknowledgment disappeared

The `http` profile adds a real transport boundary to the reliability harness: a separate
loopback HTTP service, independent client retries, and a transactional SQLite ledger.
It is a controlled local service, not a production deployment or an LLM benchmark.

```sh
uv run --no-sync agent-reliability run --profile http --output runs/http
```

The service commits the charge and then closes the TCP connection **before sending response
headers**. The client sees a transport failure and retries the same operation key. After the
service process exits, the grader reads the ledger directly and checks the number of effects,
authorized amount, response statuses, and receipt-to-row correspondence. A returned HTTP 200
alone cannot pass the test.

| Scenario | Guarded implementation | Deliberately unguarded control |
|---|---|---|
| Clean request | One charge | One charge |
| Lost acknowledgment after commit | Retry returns original charge | Retry writes a duplicate; oracle catches it |
| Four concurrent requests for the same key | One transactionally deduplicated charge | Four charges; oracle catches them |
| Same key with a different amount | HTTP 409; original charge unchanged | Additional unauthorized amount; oracle catches it |

Recorded run: **8/8 expected outcomes**, comprising **5 completed tasks** and **3 correctly
detected invariant violations**. These counts are separate from the existing 42-case `full`
profile and the Docker code-validation showcase. [Raw results](../examples/http-transport/results.json)
include transport attempt histories and durable fault coverage; the adjacent snapshots expose
the actual committed rows.

## What the experiment establishes

The guarded service serializes lookup and insertion with `BEGIN IMMEDIATE`, binding an
operation key to its original amount. The control deliberately omits this check. The test
covers lost acknowledgments, concurrency, and conflicting retries without substituting a
Python exception for the client's HTTP call. The scheduled fault is still deterministic;
it does not model arbitrary network partitions, multi-host failover, authentication, or
power-loss durability. All requests and fixed test amounts are local fixtures.

The subprocess uses an ephemeral loopback port, bounded startup/client deadlines, and cleanup
in a `finally` block. CI runs both this profile and the existing independent recovery suites.
