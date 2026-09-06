# Residency photograph data/capacity/photo-10401-20260906-204350

Sessions held: 116 across 4 instances; hold 596 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 109 | 2.13 | 63% | 128 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 207 | 108 | 118 | 108 | 108 | -0% |
| code_agent | 206 | 140 | 152 | 141 | 140 | -1% |
| deep_research | 108 | 34 | 35 | 34 | 34 | +1% |
| ingestion | 108 | 22 | 25 | 22 | 22 | -0% |
| task_ticket | 642 | 10 | 11 | 10 | 10 | -1% |

Little's law check: 2.13/s x (50 s mean + 3 s think) = 113 resident, against 116 sessions held and 109 measured in flight.
Failures in the hold: 0 of 1271.
