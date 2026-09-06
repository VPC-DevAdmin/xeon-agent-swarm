# Residency photograph data/capacity/photo-10301-20260906-213254

Sessions held: 152 across 4 instances; hold 596 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 145 | 2.30 | 90% | 158 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 224 | 136 | 139 | 136 | 136 | -0% |
| code_agent | 222 | 185 | 189 | 185 | 185 | +0% |
| deep_research | 116 | 34 | 36 | 34 | 34 | -1% |
| ingestion | 117 | 20 | 25 | 22 | 19 | -13% |
| task_ticket | 688 | 10 | 11 | 10 | 10 | -0% |

Little's law check: 2.30/s x (62 s mean + 3 s think) = 149 resident, against 152 sessions held and 145 measured in flight.
Failures in the hold: 0 of 1367.
