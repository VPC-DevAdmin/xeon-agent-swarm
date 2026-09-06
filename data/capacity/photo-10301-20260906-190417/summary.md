# Residency photograph data/capacity/photo-10301-20260906-190417

Sessions held: 116 across 4 instances; hold 449 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 103 | 3.38 | 33% | 78 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 55 | 90 | 92 | 90 | 89 | -2% |
| code_agent | 46 | 128 | 131 | 128 | 129 | +1% |
| deep_research | 67 | 35 | 36 | 35 | 35 | -0% |
| ingestion | 103 | 22 | 25 | 22 | 21 | -7% |
| task_ticket | 1249 | 10 | 11 | 10 | 10 | +0% |

Little's law check: 3.38/s x (19 s mean + 3 s think) = 73 resident, against 116 sessions held and 103 measured in flight.
Failures in the hold: 0 of 1520.
