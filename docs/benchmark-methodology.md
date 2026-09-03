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
digest, one data analyst, and two task agents (six sessions). The task
agent is weighted twice because short-lived agents are most of a
deployment. That weight is a declared assumption; the per-component cost
table (section 8) lets a reader rescale to any mix. At the reference mix a
workflow moves about 20,000 tokens through the model tier and generates
about 1,300.

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
other. The reference allocation is 14 cores for the reranker, 2 for the
embedder, and the remaining 48 for the four instances, their executors,
stand-ins, and databases, which are pinned there so nothing shares AMX
units with the tier. Pinning is by cpuset, not quota: a CPU quota on a
128-thread host lets a many-threaded process burn its allowance in
milliseconds and sleep for the rest of the period.

The allocation is set from measured costs so that both sides of the server
saturate near the same rate, which is what makes the server, rather than
an allocation, the limit. The sizing arithmetic is in section 8.

Each executor admits at most four reranker calls at a time and backs off
exponentially on a 429 or 503, so a saturated tier produces queueing the
judge sees as latency, never errors.

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
  executors' side reach saturation near the same rate; with the reference
  mix that is 14 + 2 cores for the tier and 48 for everything else.

`scripts/cost_table.py` produces the per-component table from any series;
`scripts/plateau_set_summary.py` produces the certified set summary.

## 9. Reproducibility record

Each result carries the seed, the software commit, the workload definition
(`config/capacity_scenarios.yaml`), the serving-tier parameters, rerank
depth, sandbox isolation mode, the core allocation (`allocation.env`),
the process topology, the host profile, and the ledger's SHA-256. Ledgers,
judgments, set summaries, and per-core samples are committed under
`data/capacity/`. Two results compare only when their fingerprints match.

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
