# Serving profile: Qwen/Qwen3.8-Flash via https://api.together.xyz/v1

| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |
|---|---|---|---|---|---|---|---|---|---|
| 2 | 358 | 358 | 42 | 0.39 | 87 | 1484 | 1329/6077 | 2118/11292 | 137.1 |
| 8 | 358 | 358 | 58 | 1.49 | 344 | 5681 | 1316/5898 | 2058/10941 | 137.4 |
| 32 | 358 | 358 | 142 | 2.99 | 736 | 11371 | 1304/6201 | 2108/12833 | 137.3 |

| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |
|---|---|---|---|---|---|---|
| analysis | 54 | 1844 | 55 | 1305 | 2048/6499 | 138.7 |
| general-purpose | 28 | 1298 | 131 | 1139 | 3031/10491 | 134.8 |
| judge | 40 | 798 | 58 | 1046 | 1694/6610 | 96.9 |
| planner | 128 | 7151 | 110 | 1420 | 2960/10032 | 147.4 |
| research | 54 | 1677 | 60 | 1114 | 1713/12351 | 141.9 |
| writing | 54 | 1994 | 54 | 1035 | 1496/33513 | 144.6 |

| archetype (lowest concurrency) | call positions | prompt tokens per workflow | completion tokens per workflow | completion by role |
|---|---|---|---|---|
| analyst_large | 14 | 49,538 | 1,165 | analysis 252, judge 238, planner 324, research 186, writing 166 |
| code_agent | 14 | 48,469 | 3,682 | analysis 416, judge 250, planner 368, research 544, writing 2104 |
| deep_research | 14 | 57,026 | 4,651 | analysis 214, judge 256, planner 774, research 158, writing 3249 |
| ingestion | 6 | 19,057 | 489 | general-purpose 310, judge 131, planner 48 |
| task_ticket | 5 | 17,400 | 1,215 | general-purpose 533, judge 117, planner 565 |
