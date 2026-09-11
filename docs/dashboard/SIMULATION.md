# Working-pool simulation

The offline conference demo is simulation-only. It opens at 720 working agents, using the current Enterprise proportions and the single-CPU measurement-backed sizing model. The default requires three two-socket R770s under the 10% sizing tolerance, at approximately 73.7% average CPU utilization. Percent utilization uses both sockets' total capacity; the percentage is not doubled. One footer identifies the demo as extrapolated from single-socket measurements. There is no mode pill or recorded-playback mode.

Each card has an independently editable incoming percentage and a visible remove control. One compact dashed Add agent card opens a selector containing only missing archetypes. Adding a card starts it at 0%; removing one clears only its share. No other percentage changes. The running total shows how much to add or reduce. Results hold the last valid simulation until the draft totals 100%, then update automatically. Reset to Default restores 720 agents, all five archetypes, and the original rounded percentages.

The total-agent target defaults to 720 across the deployment. Its multiplier uses that default population. Default percentages are 50%, 8%, 8%, 17%, and 17%. Memory is displayed per required R770; utilization is the average across required R770s. The hardware diagram expresses the ratio; the capacity strip reports whole systems.

## Current sources

## Illustrated detail views

`detail-views.js` and `detail-views.css` supply the click-through views, embedded by the offline build. Agent views mirror whitepaper Figures 7a–7e: distinct jobs above a shared worker cycle, then final synthesis or direct return. R770 and XE7740 clicks open separate branded server showcases, with platform specifications linked to Dell and modeled configuration values explicitly labeled. CPU views separate attributed work shares, reserved core pools, and an estimated population/utilization sweep; the sweep is not recorded time-series telemetry. Memory, model serving, and population use visual summaries. Only Testing and methodology opens extended explanatory prose.

Motion illustrates flow or reveals chart values; it does not invent measured events. Reduced-motion preferences and the demo's motion setting disable these effects. `check-details.cjs` exercises every view against the embedded data and checks zero-load handling without browser automation.

## Benchmark sources

Each card pairs completed workflows per hour beneath its work description with the live “working now” count. Reference throughput counts successful completions in the selected steady-state window, doubles it for the R770 reference, and expresses that rate per hour; it is a fixed window average, not a cumulative counter. Simulation throughput uses modeled per-archetype workflow rates across the fleet. Hover text distinguishes the two. The completion line replaces the decorative resident segments to preserve card density.

The model uses v2.3 definitions only. `simulation-profile.json` declares the current component costs from methodology sections 2, 8, and 11. The build reads call budgets, output budgets, CPU allocation, serving throughput, and measured baseline values from `../whitepaper/paper-results.json`.

The current timing source is `data/capacity/replay/enterprise-v23-12101.json`. The build selects successful workflows submitted at least 300 seconds into the capacity plateau and completed before its end. It records sample counts and hashes of the configuration, methodology, and timing evidence. Older archetype cost scripts are not used.

## Calculation

Reference mean lifetimes are calibrated to the measured resident population and model-output demand. A six-CPU reference working pool contains 45 Task, 81 Research, 64 Ingestion, 399 Analyst, and 269 Code agents. These are resident counts, not incoming proportions. Their different lifetimes reproduce the Enterprise arrival proportions.

For a chosen mix, resident allocations are proportional to incoming share multiplied by reference mean lifetime. Largest-remainder rounding preserves the requested whole-agent total. At small target populations, rounding can change the achieved incoming proportions. Removing every archetype leaves an incomplete draft; previous results remain held until a new mix totals 100%.

For each archetype, its working count divided by its target mean lifetime gives the internally required completion rate. That rate multiplies the per-workflow call, output, and component budgets. The cards show the resulting hourly completion rates.

CPU sizing takes the largest requirement across application execution, reranking, query embedding, document embedding, and memory. Dedicated services and memory use an 80 percent planning target. The application term weights relative sandbox demand at 85 percent and call-related demand at 15 percent, anchored to Enterprise capacity. Those weights are modeling assumptions awaiting mixed-workload validation.

Whole R770 counts apply a 10% CPU sizing tolerance to pairs of the tested 64-core allocation: ceil(CPU equivalents / 2 / 1.1), with a minimum of one system for nonzero work. Thus 2.2 equivalents uses one R770, while 2.2001 uses two. This is an estimated sizing policy, not guaranteed throughput. GPU sizing still rounds up to whole eight-GPU systems, preserving the model-serving requirement. CPU utilization and CPU-family shares are calibrated to the Enterprise profile, with fixed and variable runtime overhead. The simulation assumes independent socket placement. Multi-socket scaling has not been measured by this model.

Memory assigns 90 percent of the measured footprint to analyst work and 10 percent to a fixed per-CPU residual. This is an explicit approximation. Sandbox wall time includes queue time, so it cannot directly identify the number of memory-active jobs. Other archetypes' memory growth and memory bandwidth are not independently modeled.

GPU equivalents equal required output tokens per second divided by the paper's current per-GPU serving reference. Whole XE7740 counts round up to eight-GPU systems. The interface reports serving-capacity use and demand on the last system. Those values are not GPU utilization measurements.

The hardware picture normalizes demand to one eight-GPU XE7740. A multiplier gives the R770 equivalent, with up to four reference images. The capacity boxes below show whole-system counts for the selected population and additional systems beyond the initial three R770s and one XE7740.

The simulator sizes hardware to reference responsiveness. It does not predict a new-mix p95, replay invented completions, or model overload queues on a fixed fleet. Mean completion targets are available in each agent's detail panel. Workflow motion remains illustrative.

## Spot checks to run

Use the current v2.3 jobs, model-response profile, and 6/1/2/55 CPU allocation. Counts below are fixed working pools on one tested CPU, not arrival proportions. Start a replacement of the same archetype after completion.

| Check | Task | Research | Ingestion | Analyst | Code | Purpose |
|---|---:|---:|---:|---:|---:|---|
| Enterprise working pool | 8 | 13 | 11 | 66 | 45 | Check closed-pool calibration against the open-arrival reference |
| Research-heavy | 8 | 32 | 2 | 10 | 8 | Check the reranking limit and query-embedding demand |
| Ingestion-heavy | 8 | 2 | 20 | 8 | 8 | Check document embedding, OCR, and memory growth |
| Analyst-heavy | 8 | 4 | 4 | 90 | 8 | Measure active job memory, bandwidth, and CPU contention |
| Code-heavy | 8 | 4 | 4 | 8 | 100 | Check compiler contention and shared runtime overhead |
| Task-heavy | 100 | 0 | 0 | 0 | 0 | Check overhead and retrieval limits with little sandbox work |

For each configuration, run 75%, 100%, and 125% of these counts, rounding each nonzero count to at least one. Warm up, then retain at least 30 minutes of steady operation across three seeds. Record per-archetype completion counts and mean/p95 latency, CPU-family occupancy, per-tier queue waits, active sandbox jobs, private and shared memory, and memory bandwidth. Record request and output-token counts even though the UI does not foreground rates.

These runs validate the model and expose its errors. They do not assume the proposed counts are a capacity boundary. A subsequent dual-socket check is needed before treating R770 counts as validated multi-socket capacity. A model-serving check using the same recorded prompts is needed to validate GPU occupancy and latency under changed mixes.

## Checks and rebuild

Run `node docs/dashboard/build-steady-state.cjs` and `node docs/dashboard/check-simulation.cjs` from the project root. The second command checks calibration, sizing arithmetic, empty and large fleets, version guards, and preservation of the recorded replay window.
