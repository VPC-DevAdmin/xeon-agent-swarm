# Serving profile: openai/gpt-oss-20b via https://api.together.xyz/v1

| concurrency | calls | ok | 429/5xx retries | requests/s | gen tok/s | prompt tok/s | ttft p50/p95 ms | total p50/p95 ms | decode tok/s p50 |
|---|---|---|---|---|---|---|---|---|---|
| 2 | 182 | 182 | 0 | 0.27 | 169 | 1178 | 471/2384 | 5543/16920 | 109.0 |

| role (highest concurrency) | calls | prompt tok | completion tok | ttft p50 ms | total p50/p95 ms | decode tok/s |
|---|---|---|---|---|---|---|
| general-purpose | 63 | 2394 | 458 | 420 | 5187/9090 | 92.3 |
| judge | 7 | 388 | 51 | 505 | 828/1037 | 177.5 |
| planner | 112 | 5940 | 756 | 896 | 7530/17333 | 113.1 |

| archetype (lowest concurrency) | call positions | prompt tokens per workflow | completion tokens per workflow | completion by role |
|---|---|---|---|---|
| task_ticket | 5 | 15,504 | 2,194 | general-purpose 770, judge 51, planner 1373 |
