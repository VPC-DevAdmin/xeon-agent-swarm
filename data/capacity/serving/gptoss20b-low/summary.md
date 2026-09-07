# Serving profile: openai/gpt-oss-20b via https://api.together.xyz/v1

| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |
|---|---|---|---|---|---|---|---|---|---|
| 2 | 358 | 358 | 0 | 0.09 | 121 | 309 | 408/2573 | 17612/56922 | 66.9 |
| 8 | 358 | 358 | 0 | 0.40 | 507 | 1320 | 398/2362 | 15632/52510 | 69.2 |
| 32 | 358 | 358 | 0 | 1.28 | 1662 | 4179 | 391/3483 | 16724/59137 | 64.5 |

| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |
|---|---|---|---|---|---|---|
| analysis | 54 | 1548 | 1206 | 356 | 20765/65149 | 63.4 |
| general-purpose | 28 | 1051 | 317 | 359 | 5416/10591 | 64.2 |
| judge | 40 | 809 | 80 | 1240 | 1550/2889 | 102.2 |
| planner | 128 | 6186 | 1107 | 416 | 17656/61733 | 65.2 |
| research | 54 | 1402 | 922 | 356 | 14300/59040 | 64.0 |
| writing | 54 | 1691 | 2145 | 356 | 35222/51454 | 63.5 |

| archetype (lowest concurrency) | call positions | prompt tokens per workflow | completion tokens per workflow | completion by role |
|---|---|---|---|---|
| analyst_large | 14 | 42,810 | 20,484 | analysis 2308, judge 312, planner 7804, research 3232, writing 6828 |
| code_agent | 14 | 42,024 | 13,120 | analysis 2568, judge 322, planner 4628, research 1051, writing 4552 |
| deep_research | 14 | 49,172 | 21,434 | analysis 4330, judge 396, planner 5534, research 4992, writing 6180 |
| ingestion | 6 | 16,491 | 3,118 | general-purpose 804, judge 162, planner 2152 |
| task_ticket | 5 | 15,197 | 2,423 | general-purpose 957, judge 123, planner 1343 |
