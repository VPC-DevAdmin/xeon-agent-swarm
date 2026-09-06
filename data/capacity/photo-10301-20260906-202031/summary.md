# Residency photograph data/capacity/photo-10301-20260906-202031

Sessions held: 116 across 4 instances; hold 596 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 110 | 2.13 | 64% | 128 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 207 | 110 | 119 | 110 | 110 | +0% |
| code_agent | 205 | 141 | 152 | 141 | 140 | -1% |
| deep_research | 107 | 34 | 35 | 34 | 34 | -0% |
| ingestion | 108 | 19 | 23 | 19 | 19 | -3% |
| task_ticket | 642 | 10 | 11 | 10 | 10 | +0% |

Little's law check: 2.13/s x (50 s mean + 3 s think) = 113 resident, against 116 sessions held and 110 measured in flight.
Failures in the hold: 0 of 1269.
