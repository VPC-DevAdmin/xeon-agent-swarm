# Residency photograph data/capacity/photo-11401-20260908-070145

Sessions held: 152 across 4 instances; hold 1737 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 150 | 0.81 | 70% | 390 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 236 | 524 | 558 | 523 | 526 | +1% |
| code_agent | 230 | 336 | 362 | 336 | 336 | +0% |
| deep_research | 115 | 181 | 204 | 182 | 180 | -1% |
| ingestion | 116 | 147 | 162 | 146 | 149 | +2% |
| task_ticket | 708 | 30 | 35 | 29 | 30 | +2% |

Little's law check: 0.81/s x (186 s mean + 3 s think) = 153 resident, against 152 sessions held and 150 measured in flight.
Failures in the hold: 0 of 1405.
