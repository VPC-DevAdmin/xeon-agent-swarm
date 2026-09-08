# Residency photograph data/capacity/photo-12001-20260908-205808

Sessions held: 144 across 4 instances; hold 1767 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 142 | 0.82 | 66% | 368 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 251 | 507 | 552 | 505 | 507 | +0% |
| code_agent | 235 | 327 | 351 | 328 | 327 | -0% |
| deep_research | 118 | 186 | 206 | 184 | 192 | +4% |
| ingestion | 119 | 148 | 167 | 148 | 148 | -1% |
| task_ticket | 720 | 17 | 20 | 17 | 17 | -0% |

Little's law check: 0.82/s x (178 s mean + 3 s think) = 148 resident, against 144 sessions held and 142 measured in flight.
Failures in the hold: 0 of 1443.
