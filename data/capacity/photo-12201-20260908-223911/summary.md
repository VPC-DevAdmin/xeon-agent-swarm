# Residency photograph data/capacity/photo-12201-20260908-223911

Sessions held: 144 across 4 instances; hold 1738 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 142 | 0.81 | 66% | 365 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 234 | 503 | 542 | 502 | 507 | +1% |
| code_agent | 233 | 327 | 355 | 327 | 326 | -0% |
| deep_research | 119 | 193 | 211 | 193 | 194 | +1% |
| ingestion | 119 | 146 | 164 | 148 | 145 | -3% |
| task_ticket | 709 | 17 | 19 | 17 | 17 | +0% |

Little's law check: 0.81/s x (175 s mean + 3 s think) = 145 resident, against 144 sessions held and 142 measured in flight.
Failures in the hold: 0 of 1414.
