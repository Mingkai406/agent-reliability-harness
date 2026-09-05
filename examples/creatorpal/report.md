# CreatorPal reliability experiments

Synthetic controls through real application tools. Expected safe rejection counts as a scenario pass, not a completed research task. No live LLM capability is measured.

| Scenario | Adapter | Expected | Scenario passed | Task completed |
|---|---|---|---|---|
| clean | offline-adk | complete_once | True | True |
| retrieval_timeout | offline-adk | complete_once | True | True |
| malformed_retrieval | offline-adk | complete_once | True | True |
| analytics_timeout | offline-adk | reject_without_report | True | False |
| interrupted_before_publish | offline-adk | complete_once | True | True |
| lost_report_ack | offline-adk | complete_once | True | True |
| interrupted_after_publish | offline-adk | complete_once | True | True |
| false_completion | offline-adk | reject_without_report | True | False |
