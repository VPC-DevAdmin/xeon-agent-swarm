# Plateau set set-20260905-060903

Series: series-9901-20260905-060903, series-10001-20260905-070727, series-10101-20260905-080544

| rate/inst | achieved (median, range) | gen ok | keeps up | analyst large p50/p95 | code agent p50/p95 | deep research p50/p95 | task ticket p50/p95 | backlog over the hold (per series) | resident, measured (median) | resident, Little | host CPU | retrieval CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.3 | 1.2 (1.2-1.2) | yes | yes | 87/88 | 122/125 | 34/35 | 10/11 | 60->60, 60->61, 60->61 | 57 | 52 (51-52) | 32.3% | 0.0% |
| 0.4 | 1.6 (1.6-1.6) | yes | yes | 89/91 | 118/121 | 34/35 | 10/11 | 79->81, 80->81, 80->81 | 72 | 67 (67-68) | 36.7% | 1.9% |
| 0.5 | 2.0 (2.0-2.0) | yes | yes | 101/108 | 131/141 | 34/35 | 10/11 | 98->102, 99->99, 94->99 | 100 | 88 (86-89) | 51.8% | 2.0% |
| 0.6 | 2.39 (2.39-2.39) | yes | yes | 129/135 | 189/199 | 34/35 | 10/11 | 149->177, 149->177, 149->174 | 150 | 102 (102-104) | 72.8% | 1.9% |
| 0.7 | 2.79 (2.79-2.79) | yes | NO | 225/234 | -/- | 35/36 | 10/11 | 197->324, 196->322, 198->323 | 241 | 57 (57-58) | 75.1% | 2.2% |

## Capacity (the highest rate at which every series keeps up: completions pace arrivals over the hold)

- **capacity**: 2.39 workflows/s box-wide (range 2.39-2.39), 150 resident as measured, 102 by Little's law (range 102-104); the next rung, 0.7 per instance, falls behind
