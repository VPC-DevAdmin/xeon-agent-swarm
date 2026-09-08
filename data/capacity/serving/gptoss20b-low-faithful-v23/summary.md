# gpt-oss-20b (low) shape-faithful profile, v2.3

The record profile with the task agent's positions replaced by the v2.3 handoff shape: the planner delegates (planner/0), the worker looks up, records and answers (general-purpose/0, /1), one judgment (judge/0); no closing model call.

| task position | n | mean completion tokens |
|---|---|---|
| task_ticket/general-purpose/0 | 3 | 549 |
| task_ticket/general-purpose/1 | 56 | 481 |
| task_ticket/judge/0 | 7 | 52 |
| task_ticket/planner/0 | 56 | 404 |

Task agent output per workflow: about 1486 tokens.
