# Agent Reliability Harness — experiment report

Deterministic offline controls; no live model inference

**10/10 expected outcomes · 7 completed tasks · 2 safe rejections · 1 detected negative controls**

A passing scenario matches its declared expectation and injects every scheduled fault. An expected violation is a detected broken control, not a successful task.

| Case | Application | Expected | Observed | Fault schedule covered | Scenario passed |
|---|---|---|---|---|---|
| langgraph-clean | langgraph | completed | completed | True | True |
| langgraph-retrieval-timeout | langgraph | completed | completed | True | True |
| langgraph-malformed-build | langgraph | completed | completed | True | True |
| langgraph-lost-ack | langgraph | completed | completed | True | True |
| langgraph-restart-before-commit | langgraph | completed | completed | True | True |
| langgraph-restart-after-commit | langgraph | completed | completed | True | True |
| langgraph-restart-after-export | langgraph | completed | completed | True | True |
| langgraph-retry-exhaustion | langgraph | rejected | rejected | True | True |
| langgraph-permanent-failure | langgraph | rejected | rejected | True | True |
| langgraph-invalid-receipt | langgraph | violation | violation | True | True |

Local durations are diagnostic measurements, not model inference benchmarks. No model usage or cost is measured. Each case retains its snapshot, fault journal and OpenTelemetry traces.
