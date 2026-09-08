# Plateau set set-20260908-025329

Series: series-11401-20260908-025329, series-11501-20260908-034801, series-11601-20260908-044231

| rate/inst | achieved (median, range) | gen ok | keeps up | analyst large p50/p95 | code agent p50/p95 | deep research p50/p95 | ingestion p50/p95 | task ticket p50/p95 | backlog over the hold (per series) | resident, measured (median) | resident, Little | host CPU | retrieval CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.21 | 0.84 (0.84-0.84) | yes | yes | 527/563 | 343/369 | 184/202 | 152/169 | 30/35 | 156->179, 148->166, 146->164 | 152 | 108 (107-109) | 64.5% | 3.0% |
| 0.24 | 0.96 (0.96-0.96) | yes | NO | -/- | 501/546 | 188/210 | 226/256 | 31/35 | 183->268, 187->271, 184->267 | 215 | 97 (97-101) | 82.8% | 3.5% |

## Capacity (the highest rate at which every series keeps up: completions pace arrivals over the hold)

- **capacity**: 0.84 workflows/s box-wide (range 0.84-0.84), 152 resident as measured, 108 by Little's law (range 107-109); the next rung, 0.24 per instance, falls behind
