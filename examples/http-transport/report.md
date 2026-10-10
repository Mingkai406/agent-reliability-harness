# Agent Reliability Harness — experiment report

Adapter-declared execution: Local HTTP subprocess + SQLite; controlled connection loss; no model calls

**8/8 expected outcomes · 5 completed tasks · 0 safe rejections · 3 detected negative controls**

A passing scenario matches its declared expectation and injects every scheduled fault. An expected violation is a detected broken control, not a successful task.

| Case | Application | Expected | Observed | Fault schedule covered | Scenario passed |
|---|---|---|---|---|---|
| http-baseline-clean | http-charge | completed | completed | True | True |
| http-baseline-lost-ack | http-charge | violation | violation | True | True |
| http-baseline-concurrent | http-charge | violation | violation | True | True |
| http-baseline-conflict | http-charge | violation | violation | True | True |
| http-guarded-clean | http-charge | completed | completed | True | True |
| http-guarded-lost-ack | http-charge | completed | completed | True | True |
| http-guarded-concurrent | http-charge | completed | completed | True | True |
| http-guarded-conflict | http-charge | completed | completed | True | True |

Local durations are diagnostic measurements, not model inference benchmarks. No model usage or cost is measured. Each case retains its snapshot, fault journal and OpenTelemetry traces.
