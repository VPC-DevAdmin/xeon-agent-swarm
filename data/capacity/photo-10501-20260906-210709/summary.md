# Residency photograph data/capacity/photo-10501-20260906-210709

Sessions held: 116 across 4 instances; hold 596 s.

| agents resident (median of samples) | completions / s | host threads busy | host memory |
|---|---|---|---|
| 110 | 2.13 | 64% | 128 GB |

| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |
|---|---|---|---|---|---|---|
| analyst_large | 206 | 109 | 120 | 108 | 109 | +1% |
| code_agent | 209 | 142 | 154 | 141 | 142 | +1% |
| deep_research | 108 | 34 | 35 | 34 | 34 | +0% |
| ingestion | 107 | 18 | 22 | 19 | 18 | -6% |
| task_ticket | 640 | 10 | 11 | 10 | 10 | -1% |

Little's law check: 2.13/s x (51 s mean + 3 s think) = 115 resident, against 116 sessions held and 110 measured in flight.
Failures in the hold: 0 of 1270.
