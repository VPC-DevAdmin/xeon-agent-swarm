# Agent capacity benchmark methodology

This is the methodology of record for the agent-host capacity benchmark.
It describes the benchmark as it runs today: the five workflow archetypes
and why each exists, the tile they form, how the server is packed, how load
is offered, how the latency knee and the capacity cliff are found, how
verdicts are computed from evidence, and what the published numbers mean.
Everything a result claims can be recomputed from the versioned ledgers in
`data/capacity/` with the versioned judge in `backend/capacity/judge.py`.

## 1. What the benchmark measures

An **agent host** is the server that runs everything around the language
model: planning, dispatching workers, retrieving and packing context,
executing tools, validating outputs, writing durable records, carrying the
state of every session in flight. The model itself is elsewhere (a serving
tier reached over an API). The benchmark asks how much of that work one
server sustains, and reports three numbers from one curve:

- **Capacity** (the cliff): the highest offered rate at which completions
  keep pace with arrivals. Past it the backlog grows without bound whatever
  deadline anyone chooses, so no service-level choice can inflate a claim
  beyond it.
- **Certified service level** (the knee crossings): for each deadline tier,
  the highest plateau at which at least 95% of every workflow type finishes
  inside the tier's deadline, at 95% confidence jointly across types, while
  capacity also holds. A tier's certified rate is where the latency curve
  crosses that tier's line.
- **Resident sessions**: how many agents the server is carrying at a rate,
  by Little's law (rate x median workflow time plus a 3 s think time). It is
  derived at any stable operating point; a closed-loop confirmation run at
  the certified point ("residency photograph") remains available.

Every number is published with the whole curve behind it: each plateau's
per-type latency, its tier verdicts, its backlog verdict, and the failed
level above the last good one.

## 2. The workload: five archetypes and a tile

A benchmark unit must be the same size on every repetition, so each
archetype declares a **contract** (subtasks, model calls, validations, tool
calls, an input-token floor) and every completed unit is held to it; a unit
outside its contract is invalid, never counted as a success or a failure.
The five archetypes differ in which kinds of work they contain, in graded
amounts, so that each cost term in the system is identifiable from the data.

| Archetype | Role it represents | Workers | Retrievals | Sandboxed jobs | Records | Contract (calls / validations / tools) | Tokens moved per workflow | What it isolates |
|---|---|---|---|---|---|---|---|---|
| Research brief | a RAG researcher: gathers sources, drafts a recommendation | 3 | 3 | 0 | 3 | 13 / 7 / 6 | ~35,000 | retrieval cost per call |
| Comparison | an analyst with sources and a computation | 3 | 1 | 1 light | 3 | 12 / 7 / 5 | ~26,000 | the light job, and the mixed case |
| Digest | a summarizer over given material | 3 | 0 | 0 | 3 | 10 / 7 / 3 | ~20,000 | pure three-worker orchestration (the control) |
| Data analyst | a pipeline agent over data | 3 | 0 | 3 heavy | 3 | 13 / 7 / 6 | ~24,000 | the heavy job |
| Task agent | a trigger, triage, or routing agent: born, does one thing, dies | 1 | 0 | 0 | 1 | 4 / 3 / 1 | ~7,000 | per-agent lifecycle cost (planner plus synthesis, little between) |

Why these five: each is a role an enterprise actually deploys, and each
differs from every other in at least one component count. Retrieval appears
3 / 1 / 0 times across the researcher, comparison, and digest; execution
appears 3 heavy / 1 light / 0 across the analyst, comparison, and the rest;
the digest carries orchestration alone; the task agent carries the fixed
per-agent cost with almost nothing else. The comparison deliberately carries
two components so the mixed case exists in the data. Five roles is already
a lot for a reader; a sixth would not add an identifiable term.

**The tile** is the unit of load: one research brief, one comparison, one
digest, one data analyst, and two task agents (six workflow arrivals). The task
agent is weighted twice because short-lived agents are most of a
deployment. That weight is a declared assumption; the per-component cost
table (section 8) lets a reader rescale to any mix. At the reference mix a
workflow moves about 20,000 tokens through the model tier and generates
about 1,300.

The six positions describe the arrival mix, not resident concurrency.
Workflow duration determines how many sessions remain active at once.

Every workflow runs the production orchestrator end to end: a planner
delegates the declared subtasks to specialist workers (one, for the task
agent), each worker calls its tools and drafts its section, a synthesis
step combines the results, mechanical and judge validations run on every
step and on the synthesis, and steps, attempts, validations, and tool
records are written durably. Prompts are self-contained; no third-party
service participates in a measured run.

### The serving tier is modeled per call

No model call is instantaneous. A deterministic stand-in answers every call
through the production request path and waits as a remote serving tier
would: 500 ms to first token, plus output tokens at 100 per second, plus
input tokens at 8,000 per second, computed from the actual payload of that
call with 20% seeded jitter. A planner re-reading 30,000 tokens waits
several seconds; a validator returning a verdict waits about one. The
three parameters are part of the machine fingerprint. Each call re-sends
its whole context and is charged prefill for all of it; a serving tier
with prompt caching would charge less, and the benchmark keeps the
pessimistic accounting because the host's cost, the quantity measured,
does not change with the tier's cache policy.

### Retrieval is real work on the server

A worker that retrieves calls a tool that runs BM25 keyword search over a
seeded 120,000-passage store (SQLite FTS5, 2,000 topics, built once per
server), fuses that ranking by reciprocal rank with the answer of a vector
index (modeled as a 15 ms off-server call, because a large vector database
is its own system in any deployment), prefilters to sixteen candidates with
a keyword scorer, scores those sixteen with an INT8 cross-encoder
(ms-marco-MiniLM-L-6-v2) on the processor's AMX units, and packs about
6,000 words of winning passages into the worker's context with `[chunk-N]`
citations. Workers cite the passage ids they were given, so grounding is
checkable by construction. Rerank depth (sixteen) is a declared parameter
in the fingerprint; its cost law is in section 8. Retrieval quality is
reported as an in-topic fraction and never judged: capacity is invariant to
relevance, since reranking sixteen relevant passages costs what reranking
sixteen irrelevant ones costs.

### Execution is a real, bounded sandbox

A worker that executes calls a tool that runs one job in a fresh, isolated
interpreter (`python -I -S`) under CPU-time, address-space, and file-size
limits, with no network (a network namespace via `sudo unshare -n`; the
isolation mode is fingerprinted), single-threaded math, a seeded
deterministic dataset, and a few hundred characters of results returned
into the worker's context. The job is the shape of an analyst's tool run
over one day of payment events: generate the event table, join it to a
merchant table, bucket by merchant and minute, sort-based per-merchant
percentiles, a rolling load window, tail quantiles, z-scored anomaly
ranking against a category baseline, and a second pass over the flagged
merchants. Two declared sizes, calibrated on the reference server and
re-measured under load: light, 450,000 rows, about 0.3 core-seconds;
heavy, 3.3 million rows, about 1.4 core-seconds, interpreter start
included. It is representative work with a stated size, not a drain.

### The record tool

Every worker writes one durable audit row through the batched writer and
waits for its commit, then waits 50 to 150 ms derived from a checksum of
its argument, and receives about 400 characters of seeded text into its
context. A write that never landed fails the tool.

## 3. How the server is packed

Four complete orchestration instances share the server, each with one
control process, 28 executor processes, its own PostgreSQL database, and
its own deterministic model stand-in. Beside them runs the retrieval tier
the workflows call: an embedder and the reranker, served through ONNX
Runtime with dynamic batching (one inference thread per process, up to 128
pairs per run).

Cores are allocated by **whole physical core, read from the topology**. The
reference server has 64 cores with two SMT threads each and an irregular
sibling map, so allocations are never written as logical ranges. The
reranker's runtime gets one thread per physical core (the first sibling);
two runtime threads on one core's siblings were measured to halve each
other. The reference allocation is 16 cores for the reranker, 2 for the
embedder, and the remaining 46 for the four instances, their executors,
stand-ins, databases, and sandboxed jobs, which are pinned there so nothing
shares AMX units with the tier. Pinning is by cpuset, not quota: a CPU quota on a
128-thread host lets a many-threaded process burn its allowance in
milliseconds and sleep for the rest of the period.

The allocation is set from measured costs so that both sides of the server
keep headroom up to the same rate and run out together just above it,
which is what makes the server, rather than an allocation, the limit. A
tier sized to saturate exactly at the target queues at the target (a
14-core reranker sat at ~90% of its pair budget at 40 workflows/s and
queued 13 s per call); a tier sized generously starves the other side (at
20 cores the remaining 42 collapsed at the same rate, sandbox jobs alone
taking half of all hardware threads). At the reference allocation and
mix, 40 workflows/s has the executors' 46 cores 60% occupied and the tier
at about three quarters of its pair budget; at 44 the executors' side is
full on both threads of every core and the sandbox's CPU per workflow
nearly doubles (0.84 to 1.62 core-seconds) as sibling threads contend,
which is what makes the cliff sharp. The box's limit is just above 40
workflows/s whatever the split. The sizing arithmetic is in section 8.

The reranker tier is four independent server processes, each pinned to
four whole cores of its own and each with its own queue, and every
executor rotates its calls across the four per call, moving a refused
call to the next process before it backs off. The shape matters as much
as the core count: as one listening socket shared by four worker
processes, the kernel handed each keep-alive *connection*, not each
request, to a worker, and 229 of 269 executor connections landed on one
of them. That worker's queue filled at a third of the tier's pair budget
while the other three idled, which series hit it depended on how the
connections fell at warm-up, and the box looked like it had a knee at 40
workflows/s that its cores did not have. Balancing per call makes the
tier's throughput the sum of its processes.

Each executor admits at most four reranker calls at a time and backs off
exponentially on a 429 or 503, so a saturated tier produces queueing the
judge sees as latency, never errors. The tier's own queue is sized from
those gates, never set as a constant: with four instances of 28 executors
and four calls each, 448 calls may be in flight, so each of the tier's
server processes queues at least its share of that plus a margin. A queue
below that share refuses calls the cores could serve, and each refusal
costs the caller a 0.25 to 10 s backoff; a mis-sized queue once turned a
0.3 s retrieval into 5 to 7 s at 40 workflows/s and looked exactly like a
latency knee until a router probe and an executor profile showed the
router answering in its modeled wait and the executors busy only with
keyword search and database writes. The diagnostic pair, a probe of a
service from outside the executors alongside a profile of an executor,
is the standard way a knee is attributed here before it is reported.

## 4. How load is offered: plateaus

The load generator is open loop and deliberately dumb: it submits
workflows on a fixed arrival schedule that ignores completions, holds one
rate for the whole run, and reacts to nothing except two safety stops (a
host resource streak and a backlog cap of 20,000 outstanding workflows,
past which it records rejections rather than growing without limit).
Arrivals follow the tile rotation, so the mix is identical at every rate.

Rates are run as separate **plateaus**, one rate per fleet start, because
workflow latency (10 to 40 s unloaded, minutes under load) rivals any
level's dwell: a cohort admitted at one rate would otherwise finish under
the next. Holding one rate for ten minutes lets every cohort complete
under the rate that admitted it, and the latency-versus-rate curve is read
across plateaus. Exploration uses five-minute holds to find the region;
certification uses ten-minute holds under three seeds that differ by 100.

The generator keeps its own receipt in every ledger sample: arrivals shed
by its stall clamp and ticks fired late. A plateau whose achieved arrival
rate is under 95% of the offered rate is generator-limited and certifies
nothing.

## 5. Finding the knee and the cliff

The search has three stages, all producing evidence for the same judge.

1. **Exploration.** Short plateaus at a spread of rates (for example 2, 4,
   6, 8 per instance) until latency rises and then the backlog grows. This
   locates the band between the last flat plateau and the first collapsing
   one, and its stage timings say which component queues first.
2. **Certification.** Ten-minute plateaus under three seeds at the rates
   that bracket the band, judged offline. A tier's certified rate is the
   highest plateau every series sustains for that tier.
3. **The cliff.** One or two plateaus above the last keeping-up rate under
   the same seeds, so the capacity claim is a measured level with a failed
   level above it, the same standard as the certified rows.

On the reference server the curve has two distinct features. The **knee**
is where response time starts rising while completions still keep pace,
because some resource queues: the retrieval-carrying archetypes' 95th
percentile leaves the interactive line first. The **cliff** is where
arrivals outrun completions and every archetype's latency explodes,
including the ones that need neither retrieval nor execution, because CPU
is starved everywhere. A lenient deadline can certify a higher rate inside
the band between the two; nothing can certify past the cliff.

## 6. Judging from evidence

Every run streams a per-unit **ledger** as it happens: each workflow's
type, admit time, completion time, outcome, and offered rate; every
telemetry sample (host CPU, memory, per-group CPU attribution, the
generator's receipt); and a footer with the run's counters. Units still in
flight when the run stops are written as censored rows (admitted, not
finished). Weigh-in units admitted before the generator starts carry no
offered rate and are excluded from a plateau's cohort.

The **plateau judge** (rule `plateau-1`) reads one held rate from the
fleet's ledgers, pooled:

- The cohort is every generator-admitted unit after a warm-up of 1.5 times
  the slowest completed latency (so the queue has filled to its steady
  depth) and before the last arrival.
- For each deadline tier, each workflow type's on-time fraction takes a
  Wilson lower bound at a Bonferroni-split confidence (95% jointly across
  types); the tier is sustained when every type's bound clears 95%.
  Censored units count as pending while younger than the tier's deadline
  and as late once older.
- Capacity holds when the backlog (arrivals to date minus completions to
  date) grows by at most five units or 5% of the cohort across the
  cohort's span, and the generator delivered at least 95% of the offered
  rate.
- Resident sessions are rate x (median latency + 3 s think).

A unit succeeds only if it completed inside its contract; a workflow
running longer than the patience ceiling (900 s under the modeled tier) is
a counted failure. The certified figure for a tier is the median across
the three series of the highest plateau every series sustains, with the
range reported. Rule changes are applied to history by re-judging stored
ledgers, never by re-running load.

**Deadline tiers.** Six declared tiers, each named for the use case its
deadline serves: conversational 15 s, interactive 45 s, responsive 150 s,
attended 450 s, queued 1,200 s, background 3,600 s. Every plateau is
judged against all six; the paper reports the tiers a reader is likely to
size against and prints the whole curve.

### Per-unit stage accounting

Every ledger row carries the unit's own stage sums, gathered on the
executor that ran it: the model wait the stand-in modeled for each of its
calls, the retrieval pipeline and the reranker call inside it, any backoff
after a refusal, and the sandboxed jobs' wall and CPU time. A run's task
is bound to an accumulator when it starts, so the stages its parallel
workers time land on the same unit. `judge.py --stages` splits a plateau
by archetype and stage from those rows; the sums are resource time, not
the critical path (a researcher's three workers retrieve in parallel), so
the reading is across rates: the stage whose per-unit sum inflates as the
rate rises is where that archetype's slowdown lives, and what is left after
the model wait and the stages is the orchestration work the executors did
for the unit, which is where CPU starvation shows.

On the reference allocation (validation plateaus of series 8995, 300 s
holds at 40 and 44 workflows/s, same build and allocation as the
certified set; seconds per unit, medians):

| Archetype | Latency p50, 40 → 44 wf/s | Model wait (modeled) | Retrieval, sum (calls) | Rerank call, sum | Sandbox wall / CPU, sum (jobs) | Remainder: orchestration on the executors |
|---|---|---|---|---|---|---|
| Task ticket | 10.1 → 16.7 | 8.4 | – | – | – | 1.7 → 8.3 |
| Digest | 27.9 → 39.7 | 24.1 | – | – | – | 3.8 → 15.5 |
| Comparison | 31.1 → 65.5 | 26.5 | 0.33 → 1.53 (1) | 0.17 → 0.38 | 0.31 / 0.24 → 4.29 / 0.48 (1) | 4.0 → 33.2 |
| Research brief | 33.8 → 51.2 | 28.9 | 0.97 → 3.99 (3) | 0.56 → 1.26 | – | 3.9 → 18.3 |
| Data analyst | 36.7 → 136.6 | 27.8 | – | – | 4.83 / 4.60 → 72.8 / 10.1 (3) | 4.1 → 36.0 |

The model wait is identical at both rates to the hundredth of a second,
which is the instrumentation's check on itself: it is modeled, not
served. At 40 every archetype's remainder is 2 to 4 s. At 44 the analyst's
three jobs wait 73 s of wall for 10 s of CPU, the comparison's one job
4.3 s for 0.5, and the remainder grows for every archetype, the digest and
task agent included, which use neither retrieval nor the sandbox: the
executors' cores are the limit, and the reranker call (0.2 to 0.6 s at 40,
0.4 to 1.3 s at 44) is not.

### Process families and the executor spread

Every sample attributes host CPU to process families on the whole-host
basis (100% is all 128 threads): the instance's control process, its
executors, the sibling instances, the retrieval tier, the model stand-in,
the databases (every PostgreSQL process on the host), the sandboxed jobs
(charged from the executors' reaped-children time, since a 1.5 s job never
survives a process scan), and the remainder. Each sample also carries the
executors' per-process distribution (min, median, max, in percent of one
hardware thread), so an uneven pool shows where a family total would hide
it. At 40 workflows/s in steady state (series 8997): sandbox 26.3%,
retrieval 12.7%, executors 11.3% across the four instances, database
0.5%, control and stand-in 0.5%, other 0.5%; the 28 executors of an
instance run at 5 / 10 / 26 percent of a thread each. At 44 the sandbox
family doubles to 55% and the executors grow by two thirds, while the
database stays under 1%.

### The start-up transient

Every fleet run opens with a surge: the generator offers its full rate
from the first second while nothing completes for the first 30 s, so the
in-flight count overshoots its steady value by about 30%, the extra
sandbox jobs land on both threads of the application cores, each job's
CPU time doubles, and the host reads about 86% until the overshoot drains.
The drain takes about a minute at 36 workflows/s and three minutes at 40,
because the excess capacity that drains it shrinks as the rate nears the
cliff; at 44 it never drains, which is the cliff. The judge's warm-up rule
(units admitted before 1.5 times the slowest completed latency are not in
the cohort) and the ten-minute hold are what keep it out of a certified
number; a five-minute check hold at 40 sits inside it and must not be read
as steady state.

### The serving side as recorded data

The stand-in's per-call wait is a formula (time to first token, prefill
and decode rates). For a result whose serving side must be measured
rather than modeled, the workload's own calls are recorded and replayed:
a traced run writes every model call the orchestrator makes, classified
by its position in the workflow (archetype, role, phase);
`scripts/capture_query_set.py` dedupes them into a representative query
set with a few seeds' worth of content per position; and
`scripts/replay_query_set.py` sends that set to a real OpenAI-compatible
endpoint at one or more concurrency levels, streaming each call to
measure time to first token, total latency, the real tokenizer's token
counts, and any throttling, and writes the per-call record and a
summary (per-role latency and decode rate, aggregate tokens per second
per level). A benchmark run then points the stand-in at that record
(`CAPACITY_SERVING_PROFILE`, `CAPACITY_SERVING_CONCURRENCY`): every call
answers with the recorded timing and token counts for its position, the
run's fingerprint names the model and level, and the host is measured
under a serving tier that is data from a named model, repeatable, and
re-recordable for another model in minutes.

## 7. Attribution and what "utilization" means

A sampler reads `/proc` every two seconds and attributes CPU to groups on
the whole-host basis: the control process, the executors, the sibling
instances, the model stand-ins, the retrieval tier (following process
trees), the sandboxed jobs, and a residual. Sandboxed jobs live about
1.5 s, shorter than any process scan, so their CPU is charged from the
executors' reaped-children time (`cutime + cstime`), fleet-wide. Heavy
`/proc` work runs off the event loop so the sampler cannot stall the
generator.

Utilization is reported by **physical core**, sampled per hardware thread
with a core counted as busy as its busier sibling, alongside the
hardware-thread figure a monitoring tool would show. The two differ
because the reranker deliberately leaves each of its cores' second threads
idle, and because single-threaded sandbox jobs occupy cores one thread at
a time. The reranker's attributed share is reserved capacity (its runtime
threads spin on their cores whatever the load); its consumed cost is
demand over its measured pair budget.

Host power management is part of the fingerprint: the reference server
manages CPU frequency in firmware with no OS governor exposed, idles cores
at 500 to 2,500 MHz, and reaches about 3.9 GHz under load, so light-load
latencies include a ramp of tens of milliseconds.

## 8. Cost laws: the sizing tools

Each component's cost is measured on the fleet so the certified rate can
be rescaled to a different mix, depth, or job size.

- **Reranker.** The tier's ceiling is a pair budget: about 35 scored pairs
  per physical core per second (sixteen-pair calls of about 125 tokens),
  whatever the process and thread split. Sustainable rate scales inversely
  with candidates per call; checked at depths 16, 32, and 48 on a 40-core
  allocation, the measured edges sit on the line within a few percent.
- **Sandbox.** About 0.82 core-seconds per tile-weighted workflow at the
  reference mix (three heavy jobs at ~1.4 core-seconds per analyst, one
  light at ~0.3 per comparison, over six workflows).
- **Orchestration.** A roughly fixed floor of about 17 hardware threads
  across the four instances (executors, control, stand-ins, databases),
  nearly independent of rate, plus a small per-workflow marginal cost.
- **Allocation.** Cores are divided so that the tier's pair budget and the
  executors' side both keep headroom (about 70% and 85% respectively) up
  to the same rate; with the reference mix that is 16 + 2 cores for the
  tier and 46 for everything else, and the box is full near 40
  workflows/s whatever the split.
- **Per archetype** (each measured alone at two rates, floor cancelled):
  task agent 0.54 core-seconds per workflow, digest 1.16, comparison 1.99
  (0.41 light job, 0.46 rerank), research brief 3.20 (1.37 rerank across
  three calls), data analyst 6.74 (4.68 in three heavy jobs). Measured at
  light load, so upper bounds; the mixed tile's marginal cost at the
  certified point runs about a third lower.

`scripts/cost_table.py` produces the per-component table from any series;
`scripts/plateau_set_summary.py` produces the certified set summary.

## 9. Reproducibility record

Each result carries the seed, the software commit, the workload definition
(`config/capacity_scenarios.yaml`), the serving-tier parameters, rerank
depth, sandbox isolation mode, the core allocation (`allocation.env`),
the process topology, the host profile, and the ledger's SHA-256. Ledgers,
judgments, set summaries, and per-core samples are committed under
`data/capacity/`. Two results compare only when their fingerprints match.

The certified set of record on the reference server is
`data/capacity/set-20260904-070910` (seeds 8801, 8901, 9001; 4, 6, 8, 10,
11, and 12 per instance; ten-minute holds; run commit 4a81e9f, evidence
commit 43abb89): 39.8 workflows/s box-wide (39.83 to 39.86 across the
series) for every tier from interactive up, 1,274 resident (1,273 to
1,277), latencies flat from 16 to 40 workflows/s, zero failures through
40; 44 offered delivered 43.5 with the backlog doubling inside the hold,
48 delivered 44.2 with the generator shedding. The per-core samples for
the set are `data/capacity/set-8800-mpstat.log`. The two earlier sets
kept alongside it (`set-20260904-034441` on a shared-socket tier,
`set-20260903-152713` on a 14-core tier) are the record of the two
software limits found and removed on the way, not results.

## 10. Known limits

- The serving tier is modeled per payload and does not queue; inference-side
  saturation is outside the measurement, and the three serving parameters
  are an assumption recorded in the fingerprint.
- The vector index is modeled (15 ms); the keyword index, fusion, rerank,
  and packing are real. Retrieval quality is a diagnostic, not a gate.
- The tile weights, rerank depth, and job sizes are declared inputs. Their
  cost laws are published so results can be rescaled, but a published rate
  is for the declared mix.
- Runs cover a single host; multi-node coordination, failover, long soaks,
  and recovery after overload are not measured.
- Depths other than sixteen and job sizes other than the two declared are
  checked at one seed, not certified.
