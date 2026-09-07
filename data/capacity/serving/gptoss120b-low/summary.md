# Serving profile: openai/gpt-oss-120b via https://api.together.xyz/v1

| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |
|---|---|---|---|---|---|---|---|---|---|
| 8 | 358 | 352 | 139 | 0.40 | 357 | 1128 | 344/1725 | 8330/60832 | 81.4 |
| 32 | 358 | 358 | 0 | 1.96 | 1829 | 6070 | 328/1554 | 9780/42334 | 86.7 |

| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |
|---|---|---|---|---|---|---|
| analysis | 54 | 1548 | 617 | 295 | 8521/26664 | 90.2 |
| general-purpose | 28 | 1051 | 183 | 275 | 2428/4022 | 101.8 |
| judge | 40 | 808 | 63 | 738 | 1259/2736 | 110.7 |
| planner | 128 | 6186 | 1032 | 338 | 13665/49090 | 86.8 |
| research | 54 | 1402 | 514 | 292 | 7845/42334 | 65.4 |
| writing | 54 | 1691 | 1720 | 275 | 24572/45270 | 59.9 |

| archetype (lowest concurrency) | call positions | prompt tokens per workflow | completion tokens per workflow | completion by role |
|---|---|---|---|---|
| analyst_large | 14 | 39,298 | 13,419 | analysis 1642, judge 292, planner 5126, research 2230, writing 4130 |
| code_agent | 14 | 42,238 | 12,507 | analysis 1361, judge 324, planner 5308, research 1264, writing 4249 |
| deep_research | 14 | 48,676 | 16,738 | analysis 2700, judge 276, planner 5940, research 3392, writing 4430 |
| ingestion | 6 | 10,708 | 1,011 | general-purpose 457, judge 136, planner 418 |
| task_ticket | 5 | 9,390 | 1,242 | general-purpose 275, judge 118, planner 848 |
