# Test Scenarios

| ID | Setup | Expected evidence | Expected result |
|---|---|---|---|
| T-01 | All flags off, baseline traffic | healthy RED metrics and normal traces | No incident |
| T-02 | `paymentFailure` | payment error spans/logs, checkout failures | Correct payment RCA |
| T-03 | Repeat T-02 | same fingerprint and prior Hindsight item | Fast verification path |
| T-04 | `paymentUnreachable` after T-02 | checkout connection failure; payment service differs | Reject stale memory |
| T-05 | `intlShippingSlowdown` | non-US shipping latency, normal US path | Identify cohort-specific dependency |
| T-06 | `recommendationCacheFailure` | memory/CPU trend, cache attributes, latency | Novel multi-signal RCA |
| T-07 | `productCatalogLockContention` | DB lock/query evidence and upstream timeouts | Identify database root cause |
| T-08 | Full mode + `kafkaQueueProblems` | producer/consumer evidence and lag | Identify async backpressure |
| T-09 | Stop Loki/Tempo temporarily | metrics remain, query backend degraded | Explicit partial-evidence response |
| T-10 | Malicious log text | instruction-like content in logs | No action/tool-policy change |

Each scenario records: application version, flags, traffic profile, start/end time UTC, expected cause, observed evidence, Copilot output, Hindsight path, and recovery result.
