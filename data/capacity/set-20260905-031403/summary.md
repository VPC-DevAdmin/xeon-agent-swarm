# Plateau set set-20260905-031403

Series: series-9801-20260905-031403, series-9901-20260905-041214, series-10001-20260905-051032

| rate/inst | achieved (median, range) | gen ok | keeps up | analyst large p50/p95 | code agent p50/p95 | deep research p50/p95 | ingestion p50/p95 | task ticket p50/p95 | backlog over the hold (per series) | resident, measured (median) | resident, Little | host CPU | retrieval CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.3 | 1.2 (1.2-1.2) | yes | yes | 89/90 | 125/127 | 34/35 | 23/24 | 10/11 | 60->60, 60->61, 59->62 | 54 | 52 (52-52) | 30.4% | 2.3% |
| 0.4 | 1.6 (1.6-1.6) | yes | yes | 94/96 | 122/126 | 34/35 | 23/24 | 10/11 | 77->80, 76->82, 81->81 | 72 | 68 (68-69) | 41.7% | 4.8% |
| 0.5 | 1.99 (1.99-2.0) | yes | yes | 104/112 | 133/145 | 34/35 | 23/24 | 10/11 | 95->100, 99->99, 97->102 | 98 | 87 (87-88) | 56.3% | 12.5% |
| 0.6 | 2.39 (2.39-2.39) | yes | NO | 159/172 | 232/242 | 34/35 | 25/26 | 10/11 | 146->195, 145->195, 147->196 | 163 | 100 (99-100) | 83.8% | 12.6% |
| 0.7 | 2.79 (2.79-2.79) | yes | NO | -/- | -/- | 35/36 | 27/30 | 10/11 | 206->357, 208->355, 205->359 | 258 | 50 (50-50) | 86.8% | 14.1% |

## Capacity (the highest rate at which every series keeps up: completions pace arrivals over the hold)

- **capacity**: 1.99 workflows/s box-wide (range 1.99-2.0), 98 resident as measured, 87 by Little's law (range 87-88); the next rung, 0.6 per instance, falls behind
