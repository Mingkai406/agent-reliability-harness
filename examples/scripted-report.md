# Agent Reliability Harness

synthetic scripted controls

State is the oracle: success requires the right refund, no duplicate side effect, tenant isolation, and a valid receipt. All amounts are synthetic.

| Scenario | Mode | Pass | Refunds | Tool attempts |
|---|---|---|---:|---:|
| clean | baseline | True | 1 | 1 |
| clean | guarded | True | 1 | 1 |
| timeout_before_commit | baseline | True | 1 | 2 |
| timeout_before_commit | guarded | True | 1 | 2 |
| rate_limited | baseline | True | 1 | 2 |
| rate_limited | guarded | True | 1 | 2 |
| lost_response_after_commit | baseline | False | 2 | 2 |
| lost_response_after_commit | guarded | True | 1 | 2 |
| malformed_response_after_commit | baseline | False | 1 | 1 |
| malformed_response_after_commit | guarded | True | 1 | 2 |
| interrupted_after_commit | baseline | False | 2 | 2 |
| interrupted_after_commit | guarded | True | 1 | 2 |
| cross_tenant_request | baseline | True | 0 | 0 |
| cross_tenant_request | guarded | True | 0 | 0 |

Scripted results test harness mechanisms, not LLM capability. Duration is local end-to-end runtime, not provider inference latency. No measured model cost is available.
