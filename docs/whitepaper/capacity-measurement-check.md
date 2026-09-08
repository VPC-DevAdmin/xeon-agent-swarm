# Capacity measurement check

Checked September 8, 2026 against repository commit 4665ab1. The source series is `data/capacity/series-12001-20260908-165351`, seed 12001. The capacity measurements use the four `rate-0.21` instance records. The methodology supplies the physical-core result and resident population.

## Memory and CPU attribution

The sample window runs from 600 to 1,500 seconds after each instance starts. Across the four ledgers, 1,775 samples report mean memory use of 355.037 GB and a maximum of 429.3 GB. The paper rounds these to 355 GB and a peak near 430 GB.

Each instance samples the same host. The calculation averages the CPU-family samples rather than adding four host readings. Normalizing the mean family readings gives the following shares of attributed CPU work.

| Function | Raw normalized share | Graphic share |
|---|---:|---:|
| Sandboxed jobs | 71.08995% | 71.1% |
| Retrieval and embedding | 2.58771% | 2.6% |
| Agent execution | 23.43381% | 23.4% |
| Database and other services | 2.88853% | 2.9% |

Agent execution combines the control, executors, and sibling-instance fields. Supporting services combine database, other, and model-stand-in fields. These shares total 100% of attributed work. The separate 85% headline uses the methodology’s physical-core occupancy measure.

The methodology lists whole-number shares that total 101% after rounding. The graphic uses the normalized sample calculation, rounded to one decimal place, so its segments sum to 100%.

## Model demand

The repository’s `scripts/ratio_from_profile.py` host calculation uses the capacity files, the `enterprise-v23.jsonl` query set, and the `gptoss20b-low-faithful-v23/calls.jsonl` judgment profile. It returns 6,714 output tokens per workflow on the declared mixture and 5,658 output tokens per second. The paper reports 5,660 output tokens per second.

The mixture requires 119 model calls across twelve incoming workflows, including judgments. At the script’s reported achieved rate of 0.843 workflows per second, that is 8.36 model calls per second, rounded to 8.4. This is the mix’s serving requirement, not a counter of completed calls over an arbitrary sample interval.

| Archetype | Output tokens including judgments | Paper value |
|---|---:|---:|
| Task | 1,386 | 1,390 |
| Research | 16,082 | 16,100 |
| Ingestion | 3,666 | 3,670 |
| Analyst | 13,431 | 13,430 |
| Code | 12,821 | 12,820 |

The raw-file calculation refines the methodology’s Analyst and Code approximations of 13,380 and 12,850. Their rounded CPU-work-per-token values remain 14 and 12. The paper retains the methodology’s approximate standalone CPU-work totals and its 54.5 busy-core result at capacity. The per-core sampler log is not included in this series directory.

The chosen GPU serving rate remains 3,500 output tokens per second. Dividing 5,660 by 3,500 gives 1.62 GPU equivalents, shown as 1.6 in the narrative. Dividing 54,500 core-ms per second by 5,660 output tokens per second gives 9.6 core-ms per output token.
