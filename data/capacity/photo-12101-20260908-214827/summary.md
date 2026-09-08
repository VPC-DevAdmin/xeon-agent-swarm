# Residency photograph data/capacity/photo-12101-20260908-214827

Sessions held: 144 across 4 instances; hold 1708 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 142 | 0.82 | 66% | 365 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 239 | 506 | 558 | 508 | 503 | -1% |
| code_agent | 226 | 329 | 355 | 327 | 331 | +1% |
| deep_research | 116 | 192 | 204 | 191 | 193 | +1% |
| ingestion | 114 | 145 | 162 | 147 | 145 | -2% |
| task_ticket | 697 | 17 | 28 | 17 | 17 | -2% |

Little's law check: 0.82/s x (177 s mean + 3 s think) = 147 resident, against 144 sessions held and 142 measured in flight.
Failures in the hold: 0 of 1392.
