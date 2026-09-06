# Plateau set set-20260905-090413

Series: series-10001-20260905-090413, series-10101-20260905-100300, series-10201-20260905-110159

| rate/inst | achieved (median, range) | gen ok | keeps up | analyst large p50/p95 | deep research p50/p95 | ingestion p50/p95 | task ticket p50/p95 | backlog over the hold (per series) | resident, measured (median) | resident, Little | host CPU | retrieval CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 1.99 (1.99-2.0) | yes | yes | 96/97 | 34/35 | 22/24 | 10/11 | 66->79, 81->79, 77->80 | 73 | 72 (70-72) | 40.8% | 12.6% |
| 0.65 | 2.59 (2.59-2.59) | yes | yes | 173/183 | 34/36 | 26/28 | 10/11 | 125->147, 123->145, 127->146 | 137 | 106 (106-106) | 72.5% | 15.6% |
| 0.8 | 3.19 (3.19-3.19) | yes | NO | -/- | 35/36 | 82/100 | 10/11 | 203->327, 207->331, 207->331 | 254 | 78 (77-80) | 89.4% | 16.9% |
| 0.95 | 3.78 (3.78-3.78) | yes | NO | -/- | 35/37 | 114/217 | 10/11 | 301->528, 301->520, 306->513 | 369 | 91 (89-91) | 89.8% | 17.4% |
| 1.1 | 4.37 (4.36-4.37) | yes | NO | -/- | 37/40 | 150/218 | 11/12 | 443->580, 442->586, 449->580 | 493 | 95 (93-97) | 90.2% | 17.7% |

## Capacity (the highest rate at which every series keeps up: completions pace arrivals over the hold)

- **capacity**: 2.59 workflows/s box-wide (range 2.59-2.59), 137 resident as measured, 106 by Little's law (range 106-106); the next rung, 0.8 per instance, falls behind
