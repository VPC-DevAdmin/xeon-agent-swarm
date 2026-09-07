# Serving profile: openai/gpt-oss-20b via https://api.together.xyz/v1

| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |
|---|---|---|---|---|---|---|---|---|---|
| 8 | 358 | 358 | 0 | 0.34 | 552 | 1125 | 408/14283 | 18284/59909 | 79.8 |
| 32 | 358 | 358 | 0 | 1.17 | 1909 | 3833 | 394/13178 | 20034/56606 | 77.3 |

| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |
|---|---|---|---|---|---|---|
| analysis | 54 | 1548 | 1080 | 356 | 14768/47430 | 74.3 |
| general-purpose | 28 | 1051 | 330 | 347 | 5206/11555 | 76.8 |
| judge | 40 | 809 | 286 | 4072 | 4582/6604 | 549.2 |
| planner | 128 | 6186 | 1808 | 429 | 26303/60898 | 75.2 |
| research | 54 | 1402 | 1100 | 348 | 15563/53287 | 76.9 |
| writing | 54 | 1691 | 2254 | 374 | 29794/49966 | 77.0 |

| archetype (lowest concurrency) | call positions | prompt tokens per workflow | completion tokens per workflow | completion by role |
|---|---|---|---|---|
| analyst_large | 14 | 42,810 | 28,686 | analysis 4518, judge 1176, planner 11095, research 2852, writing 9044 |
| code_agent | 14 | 42,024 | 22,260 | analysis 3853, judge 1222, planner 5794, research 5036, writing 6354 |
| deep_research | 14 | 49,172 | 27,342 | analysis 4616, judge 1160, planner 6782, research 7336, writing 7448 |
| ingestion | 6 | 16,491 | 7,821 | general-purpose 1808, judge 455, planner 5558 |
| task_ticket | 5 | 15,197 | 3,424 | general-purpose 1010, judge 279, planner 2136 |
