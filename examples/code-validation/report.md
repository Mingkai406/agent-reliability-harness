# Agent Reliability Harness — experiment report

Adapter-declared execution: Git patch policy validation; Real Docker execution; evaluator-owned checks; no model calls

**8/8 expected outcomes · 1 completed tasks · 7 safe rejections · 0 detected negative controls**

A passing scenario matches its declared expectation and injects every scheduled fault. An expected violation is a detected broken control, not a successful task.

| Case | Application | Expected | Observed | Fault schedule covered | Scenario passed |
|---|---|---|---|---|---|
| code-valid | code-validation | completed | completed | True | True |
| code-syntax | code-validation | rejected | rejected | True | True |
| code-type | code-validation | rejected | rejected | True | True |
| code-unit | code-validation | rejected | rejected | True | True |
| code-integration | code-validation | rejected | rejected | True | True |
| code-static | code-validation | rejected | rejected | True | True |
| code-timeout | code-validation | rejected | rejected | True | True |
| code-protected | code-validation | rejected | rejected | True | True |

Local durations are diagnostic measurements, not model inference benchmarks. No model usage or cost is measured. Each case retains its snapshot, fault journal and OpenTelemetry traces.
