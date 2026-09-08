# Residency photograph data/capacity/photo-11601-20260908-084324

Sessions held: 152 across 4 instances; hold 1769 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 150 | 0.81 | 70% | 386 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 238 | 524 | 562 | 522 | 529 | +1% |
| code_agent | 235 | 339 | 365 | 340 | 338 | -0% |
| deep_research | 118 | 187 | 204 | 184 | 188 | +2% |
| ingestion | 120 | 153 | 166 | 147 | 154 | +5% |
| task_ticket | 716 | 30 | 35 | 29 | 30 | +2% |

Little's law check: 0.81/s x (187 s mean + 3 s think) = 153 resident, against 152 sessions held and 150 measured in flight.
Failures in the hold: 0 of 1427.
