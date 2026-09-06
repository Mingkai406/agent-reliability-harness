# Agent Reliability Harness — experiment report

Deterministic offline controls; no live model inference

**32/32 expected outcomes · 24 completed tasks · 4 safe rejections · 4 detected negative controls**

A passing scenario matches its declared expectation and injects every scheduled fault. An expected violation is a detected broken control, not a successful task.

| Case | Application | Expected | Observed | Fault schedule covered | Scenario passed |
|---|---|---|---|---|---|
| refund-baseline-clean | refund | completed | completed | True | True |
| refund-baseline-timeout | refund | completed | completed | True | True |
| refund-baseline-rate-limit | refund | completed | completed | True | True |
| refund-baseline-lost-ack | refund | violation | violation | True | True |
| refund-baseline-malformed | refund | violation | violation | True | True |
| refund-baseline-restart | refund | violation | violation | True | True |
| refund-baseline-tenant-denial | refund | rejected | rejected | True | True |
| refund-guarded-clean | refund | completed | completed | True | True |
| refund-guarded-timeout | refund | completed | completed | True | True |
| refund-guarded-rate-limit | refund | completed | completed | True | True |
| refund-guarded-lost-ack | refund | completed | completed | True | True |
| refund-guarded-malformed | refund | completed | completed | True | True |
| refund-guarded-restart | refund | completed | completed | True | True |
| refund-guarded-tenant-denial | refund | rejected | rejected | True | True |
| artifact-clean | artifact | completed | completed | True | True |
| artifact-retrieval-timeout | artifact | completed | completed | True | True |
| artifact-rate-limit | artifact | completed | completed | True | True |
| artifact-malformed-data | artifact | completed | completed | True | True |
| artifact-malformed-build | artifact | completed | completed | True | True |
| artifact-lost-ack | artifact | completed | completed | True | True |
| artifact-restart-before-commit | artifact | completed | completed | True | True |
| artifact-restart-after-commit | artifact | completed | completed | True | True |
| artifact-restart-after-export | artifact | completed | completed | True | True |
| artifact-retry-exhaustion | artifact | rejected | rejected | True | True |
| creatorpal-clean | creatorpal | completed | completed | True | True |
| creatorpal-retrieval-timeout | creatorpal | completed | completed | True | True |
| creatorpal-malformed-retrieval | creatorpal | completed | completed | True | True |
| creatorpal-analytics-failure | creatorpal | rejected | rejected | True | True |
| creatorpal-restart-before-publish | creatorpal | completed | completed | True | True |
| creatorpal-lost-ack | creatorpal | completed | completed | True | True |
| creatorpal-restart-after-publish | creatorpal | completed | completed | True | True |
| creatorpal-false-completion | creatorpal | violation | violation | True | True |

Local durations are diagnostic measurements, not model inference benchmarks. No model usage or cost is measured. Each case retains its snapshot, fault journal and OpenTelemetry traces.
