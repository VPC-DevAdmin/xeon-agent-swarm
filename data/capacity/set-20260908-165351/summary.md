# Plateau set set-20260908-165351

Series: series-12001-20260908-165351, series-12101-20260908-181459, series-12201-20260908-193602

| rate/inst | achieved (median, range) | gen ok | keeps up | analyst large p50/p95 | code agent p50/p95 | deep research p50/p95 | ingestion p50/p95 | task ticket p50/p95 | backlog over the hold (per series) | resident, measured (median) | resident, Little | host CPU | retrieval CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.18 | 0.72 (0.72-0.72) | yes | yes | 379/399 | 284/299 | 192/210 | 133/147 | 17/19 | 107->102, 100->107, 108->101 | 105 | 89 (86-89) | 42.5% | 2.2% |
| 0.21 | 0.84 (0.84-0.84) | yes | yes | 497/533 | 327/356 | 192/212 | 154/170 | 17/26 | 134->152, 133->150, 137->146 | 143 | 100 (100-102) | 60.5% | 2.7% |
| 0.225 | 0.9 (0.9-0.9) | yes | NO | 615/642 | 386/417 | 194/213 | 175/202 | 17/26 | 153->197, 150->197, 157->194 | 171 | 103 (102-104) | 73.7% | 2.7% |

## Capacity (the highest rate at which every series keeps up: completions pace arrivals over the hold)

- **capacity**: 0.84 workflows/s box-wide (range 0.84-0.84), 143 resident as measured, 100 by Little's law (range 100-102); the next rung, 0.225 per instance, falls behind
