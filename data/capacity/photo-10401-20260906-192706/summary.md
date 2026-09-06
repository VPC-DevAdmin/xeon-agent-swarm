# Residency photograph data/capacity/photo-10401-20260906-192706

Sessions held: 116 across 4 instances; hold 448 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 100 | 3.37 | 32% | 78 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 56 | 90 | 92 | 90 | 88 | -2% |
| code_agent | 45 | 128 | 132 | 128 | 129 | +0% |
| deep_research | 65 | 34 | 36 | 34 | 35 | +3% |
| ingestion | 100 | 23 | 26 | 23 | 20 | -11% |
| task_ticket | 1246 | 10 | 11 | 10 | 10 | -3% |

Little's law check: 3.37/s x (19 s mean + 3 s think) = 73 resident, against 116 sessions held and 100 measured in flight.
Failures in the hold: 0 of 1512.
