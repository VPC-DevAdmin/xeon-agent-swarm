# Residency photograph data/capacity/photo-10501-20260906-194955

Sessions held: 116 across 4 instances; hold 449 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 103 | 3.37 | 32% | 78 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 55 | 90 | 91 | 90 | 89 | -2% |
| code_agent | 46 | 128 | 131 | 129 | 128 | -0% |
| deep_research | 65 | 34 | 36 | 34 | 34 | -0% |
| ingestion | 99 | 22 | 26 | 23 | 21 | -11% |
| task_ticket | 1246 | 10 | 11 | 10 | 10 | +0% |

Little's law check: 3.37/s x (19 s mean + 3 s think) = 73 resident, against 116 sessions held and 103 measured in flight.
Failures in the hold: 0 of 1511.
