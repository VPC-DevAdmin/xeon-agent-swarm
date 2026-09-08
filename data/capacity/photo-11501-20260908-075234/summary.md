# Residency photograph data/capacity/photo-11501-20260908-075234

Sessions held: 152 across 4 instances; hold 1767 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 150 | 0.80 | 71% | 390 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 237 | 528 | 560 | 527 | 529 | +0% |
| code_agent | 233 | 337 | 369 | 337 | 338 | +0% |
| deep_research | 117 | 183 | 200 | 184 | 181 | -2% |
| ingestion | 119 | 146 | 164 | 146 | 145 | -1% |
| task_ticket | 716 | 31 | 35 | 31 | 31 | +1% |

Little's law check: 0.80/s x (186 s mean + 3 s think) = 152 resident, against 152 sessions held and 150 measured in flight.
Failures in the hold: 0 of 1422.
