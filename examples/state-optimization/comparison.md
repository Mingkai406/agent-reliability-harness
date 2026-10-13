# State recovery comparison

Offline deterministic two-record workflow; fresh process recovery; no LLM calls. Timing includes interpreter/framework startup. Payload bytes are distinct from allocated SQLite file size.

| Mode | Trials | Median recovery (ms) | p95 recovery (ms) | Checkpoint payload (bytes) | SQLite file (bytes) |
|---|---:|---:|---:|---:|---:|
| inline | 10 | 324.5 | 380.6 | 7283.5 | 32768.0 |
| compact | 10 | 331.3 | 437.8 | 7082.0 | 32768.0 |

Every run independently checks database commits, aggregate, exported bytes and receipt.
Latency is reported as observed; compact state is not assumed to be faster.
