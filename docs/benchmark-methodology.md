# Agent capacity benchmark methodology

This is the methodology of record for the agent-host capacity benchmark.
It describes the benchmark as it runs today: the five agent archetypes
and why each exists, the three organisation tiles they form, how the
server is packed, how load is offered, how the latency knee and the
capacity cliff are found, how verdicts are computed from evidence, what
the published numbers mean, and how the host's work per generated token
sets the ratio of orchestration servers to GPUs. Everything a result
claims can be recomputed from the versioned ledgers in `data/capacity/`
with the versioned judge in `backend/capacity/judge.py`.

## 1. What the benchmark measures

An **agent host** is the server that runs everything around the language
model: planning, dispatching workers, retrieving and packing context,
executing tools, building and testing code, parsing and embedding
documents, validating outputs, writing durable records, carrying the
state of every agent in flight. The model itself is elsewhere (a serving
tier reached over an API). The benchmark asks how much of that work one
server sustains, and reports three numbers from one curve:

- **Capacity** (the cliff): the highest offered rate at which completions
  keep pace with arrivals. Past it the backlog grows without bound, so no
  latency allowance can inflate a claim beyond it.
- **The response curve** (the knee): completion latency by agent type,
  p50 and p95, at every offered rate up to and past capacity. Where it
  starts to rise is the knee; a reader with a latency requirement of
  their own reads their rate off the curve.
- **Resident agents**: how many agents the server is carrying at a rate,
  by Little's law (rate x mean time in system plus a 3 s think time). It
  is derived at any stable operating point.

A fourth number follows from the first: **host work per generated
token**, the busy cores behind the tokens the tile makes the serving tier
generate, which sets how many GPUs one orchestration server keeps busy
(section 11).

Every number is published with the whole curve behind it: each plateau's
per-type latency, its backlog verdict, and the failed level above the
last good one.

## 2. The workload: five archetypes and three organisation tiles

A benchmark unit must be the same size on every repetition, so each
archetype declares a **contract** (subtasks, model calls, validations, tool
calls, an input-token floor) and every completed unit is held to it; a unit
outside its contract is invalid, never counted as a success or a failure.
The five archetypes are roles an enterprise deploys, at the sizes it runs
them: sizes are declared parameters and are stated with every result. The
contract shapes and model-call sizes are shared, so tokens per workflow
stay in the same range across archetypes and only the host work per
token changes.

| Archetype | Declared size | Workers | Model calls | Validations | Host work per workflow | Generated tokens | Core-ms per token |
|---|---|---|---|---|---|---|---|
| Task agent | one short request, one record | 1 | 4 | 3 | 0.5 core-s | 550 | 0.9 |
| Research agent | three retrievals at rerank depth 128 | 3 | 13 | 7 | 8.5 core-s | 1,800 | 4.7 |
| Ingestion agent | 100 PDF pages parsed, about 480 chunks embedded and indexed | 1 | 5 | 3 | 24 core-s | 550 | 44 |
| Data analyst | three sandboxed jobs over 40 million rows each | 3 | 13 | 7 | 54 core-s | 1,790 | 30 |
| Code agent | three build-and-test steps over Lua 5.4.7 and the SQLite 3.50.4 amalgamation | 3 | 13 | 7 | 92 core-s | 1,800 | 51 |

Host work is the stand-alone measurement from each archetype's cost run
(the method of section 8); in a mix, busy cores run at about 0.8 times
the summed weights because sibling threads share physical cores.

- **Task agent**: a trigger, triage, or routing agent: born, does one
  thing, dies. One worker interprets a short request, updates a work
  record, and is validated; no retrieval, no sandbox. It carries the
  per-agent lifecycle cost (planner plus synthesis, little between) and
  is most of any deployment by count. Latency 10 s.
- **Research agent**: three workers each retrieve over the corpus with
  the cross-encoder scoring 128 candidates per call, draft a section, and
  a synthesis step assembles the brief. Reranking is about 7 core-s per
  workflow (128-pair calls batch well on the AMX units). Latency 34 s at
  every load below the cliff.
- **Ingestion agent**: one worker parses 100 PDF pages in the sandbox
  (about 1 core-s), then the executor embeds the chunks on the CPU
  embedder (about 22 core-s; MiniLM-L6 in FP32 does about 22 chunks per
  second per core) and indexes them; the check query is the verifier.
  Ingestion is embedding-bound, which is why the ingest embedder is its
  own tier. Latency 23 s.
- **Data analyst**: three workers each run a sandboxed job over 40
  million rows of payment events, a week's worth; about 17 core-s per job
  stand-alone, 21 to 24 in a mix. The three jobs run one after another,
  so the archetype takes about 90 s at light load; that is its shape, not
  a queue.
- **Code agent**: three workers each build a real working tree from
  vendored source in the sandbox, Lua 5.4.7 through its own Makefile and
  the SQLite amalgamation, both with gcc -O2, and run both suites (Lua's
  own tests; an integration script against the built engine). About 33
  core-s per step, in three sequential steps, so about two minutes at
  light load; nothing in the tree is generated.

Why these five: each is a role a reader recognises and would deploy, and
each carries a different kind of host work in a different amount, so the
cost of retrieval, embedding, data jobs, and builds is each identifiable
from the data, and the per-agent lifecycle cost is carried alone by the
task agent. Five roles is already a lot for a reader; variants of a role
at other sizes are not archetypes.

### The tiles

The unit of load is a **tile** of twelve workflow arrivals. Three tiles
describe three organisations; small agents dominate by count, as they do
in a deployment, and the compute-carrying archetypes set the host work
per token.

| Tile | Task agents | Code agents | Data analysts | Research agents | Ingestion agents |
|---|---|---|---|---|---|
| Enterprise (a technology-forward company) | 6 | 2 | 2 | 1 | 1 |
| Engineering (an engineering organisation) | 7 | 3 | 1 | 1 | 0 |
| Analytics (a data and research organisation) | 6 | 0 | 3 | 2 | 1 |

Twelve positions describe the arrival mix, not resident concurrency.
Workflow duration determines how many agents remain active at once. The
tile is selected with `CAPACITY_E2E_TILE=enterprise`, `engineering`, or
`analytics` and rides the run fingerprint; the workflows and tiles are
declared in `config/capacity_scenarios.yaml`.

Every workflow runs the production orchestrator end to end: a planner
delegates the declared subtasks to specialist workers (one, for the task
and ingestion agents), each worker calls its tools and drafts its
section, a synthesis step combines the results, mechanical and judge
validations run on every step and on the synthesis, and steps, attempts,
validations, and tool records are written durably. Prompts are
self-contained; no third-party service participates in a measured run.

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
does not change with the tier's cache policy. Validations are calls to
the serving tier like any other; no small-model inference runs on the
host beyond the retrieval and embedding tiers (section 11).

### Retrieval is real work on the server

A worker that retrieves calls a tool that runs BM25 keyword search over a
seeded 120,000-passage store (SQLite FTS5, 2,000 topics, built once per
server), fuses that ranking by reciprocal rank with the answer of a vector
index (modeled as a 15 ms off-server call, because a large vector database
is its own system in any deployment), prefilters to 128 candidates with a
keyword scorer, scores those 128 with an INT8 cross-encoder
(ms-marco-MiniLM-L-6-v2) on the processor's AMX units, and packs about
6,000 words of winning passages into the worker's context with `[chunk-N]`
citations. Workers cite the passage ids they were given, so grounding is
checkable by construction. Rerank depth (128) is a declared parameter in
the fingerprint; its cost law is in section 8. Retrieval quality is
reported as an in-topic fraction and never judged: capacity is invariant to
relevance, since reranking 128 relevant passages costs what reranking 128
irrelevant ones costs.

### Execution is a real, bounded sandbox

A worker that executes calls a tool that runs one job in a fresh, isolated
interpreter (`python -I -S`) under CPU-time, address-space, and file-size
limits, with no network (a network namespace via `sudo unshare -n`; the
isolation mode is fingerprinted), single-threaded math, seeded
deterministic inputs, and a few hundred characters of results returned
into the worker's context. Three kinds of job exist, one per archetype
that executes:

- The **data job** (data analyst) is the shape of an analyst's tool run
  over a week of payment events: generate the 40-million-row event table,
  join it to a merchant table, bucket by merchant and minute, sort-based
  per-merchant percentiles, a rolling load window, tail quantiles,
  z-scored anomaly ranking against a category baseline, and a second pass
  over the flagged merchants. About 17 core-s stand-alone.
- The **build job** (code agent) copies the vendored Lua 5.4.7 tree and
  the SQLite 3.50.4 amalgamation into a working directory, builds Lua
  through its own Makefile and SQLite from `sqlite3.c` with its shell,
  both with gcc -O2, runs Lua's own test suite in its portable mode and an
  integration script against the built engine (schema, 300,000 inserted
  rows, an index, aggregates, an integrity check), and reports the build
  and test result. About 33 core-s; the suites' result is the verifier.
- The **ingest job** (ingestion agent) opens a seeded selection of PDF
  documents, extracts 100 pages of text, normalizes it, splits it into
  180-word chunks with a 30-word overlap, drops duplicates, and hands
  about 480 chunks back to the executor, which embeds them on the ingest
  embedder and indexes them for search. About 1 core-s of parsing plus 22
  of embedding.

Job sizes are declared parameters (the row count, the vendored project,
`CAPACITY_INGEST_PAGES`) and are stated with the result. It is
representative work with a stated size, not a drain.

### The record tool

Every worker writes one durable audit row through the batched writer and
waits for its commit, then waits 50 to 150 ms derived from a checksum of
its argument, and receives about 400 characters of seeded text into its
context. A write that never landed fails the tool.

## 3. How the server is packed

Four complete orchestration instances share the server, each with one
control process, 28 executor processes, its own PostgreSQL database, and
its own deterministic model stand-in. Beside them run the tiers the
workflows call: the reranker, the query embedder, and the ingest
embedder, served through ONNX Runtime and TEI with dynamic batching (one
inference thread per physical core).

Cores are allocated by **whole physical core, read from the topology**. The
reference server has 64 cores with two SMT threads each and an irregular
sibling map, so allocations are never written as logical ranges. The
reranker's runtime gets one thread per physical core (the first sibling);
two runtime threads on one core's siblings were measured to halve each
other. The allocation of record is 8 cores for the reranker (one process,
eight inference threads), 2 for the query embedder, 8 for the ingest
embedder, and the remaining 46 for the four instances, their executors,
stand-ins, databases, and sandboxed jobs, which are pinned there so nothing
shares AMX units with the tiers. Provisioning selects it with
`RERANK_PHYS_CORES=8 RERANK_WORKERS=1 RERANK_THREADS=8 EMBED_PHYS_CORES=2
INGEST_EMBED_PHYS_CORES=8`, and it rides the run fingerprint
(`allocation.env`). Pinning is by cpuset, not quota: a CPU quota on a
128-thread host lets a many-threaded process burn its allowance in
milliseconds and sleep for the rest of the period.

The allocation is set from measured costs so that the tiers keep headroom
past the rate at which the executors' side runs out, which is what makes
the server, rather than an allocation, the limit: a tier sized to
saturate at the target queues at the target and shows a knee the cores do
not have, and a tier sized generously starves the other side. At
2.0 workflows/s in all three tiles the executors' 46 cores are 83 to 84%
occupied (time-averaged per core, busier sibling), the reranker at most
36% of its cores, and the ingest embedder about 50%; past the cliff the
executors' side is 97 to 100% occupied and full on both threads of every
core, and the data jobs' and builds' CPU per workflow rises by a third to
a half as sibling threads contend (the analyst's three jobs take 72 core-s
at 2.0 workflows/s and 100 at 2.4; the code agent's three builds 102 and
151), which is what makes the cliff sharp. The ingest embedder reaches
94% only past the analytics tile's cliff. The sizing arithmetic is in
section 8.

The reranker tier is one server process pinned to eight whole cores with
its own queue; when the tier has several processes, every executor
rotates its calls across them per call, moving a refused call to the next
process before it backs off. The shape matters as much as the core count:
one listening socket shared by several worker processes hands each
keep-alive *connection*, not each request, to a worker, so a few
executors' connections can pile onto one worker while the others idle,
and the box looks like it has a knee its cores do not have. Balancing per
call makes the tier's throughput the sum of its processes.

Each executor admits at most four reranker calls at a time and backs off
exponentially on a 429 or 503, so a saturated tier produces queueing the
judge sees as latency, never errors. The tier's own queue is sized from
those gates, never set as a constant: with four instances of 28 executors
and four calls each, 448 calls may be in flight, so each of the tier's
server processes queues at least its share of that plus a margin. A queue
below that share refuses calls the cores could serve, and each refusal
costs the caller a 0.25 to 10 s backoff that looks exactly like a latency
knee. The diagnostic pair, a probe of a service from outside the executors
alongside a profile of an executor, is the standard way a knee is
attributed here before it is reported.

## 4. How load is offered: plateaus

The load generator is open loop and deliberately dumb: it submits
workflows on a fixed arrival schedule that ignores completions, holds one
rate for the whole run, and reacts to nothing except two safety stops (a
host resource streak and a backlog cap of 20,000 outstanding workflows,
past which it records rejections rather than growing without limit).
Arrivals follow the tile rotation, so the mix is identical at every rate.

Rates are run as separate **plateaus**, one rate per fleet start, because
workflow latency (10 s for a task agent to about two minutes for a code
agent unloaded, more under load) rivals any level's dwell: a cohort
admitted at one rate would otherwise finish under the next. Holding one
rate for ten minutes lets every cohort complete under the rate that
admitted it, and the latency-versus-rate curve is read across plateaus.
Exploration uses five-minute holds to find the region; the result of
record uses ten-minute holds under three seeds that differ by 100. Rates are offered
per instance (four instances, so 0.5 per instance is 2.0 box-wide) and
may be fractional.

The generator keeps its own receipt in every ledger sample: arrivals shed
by its stall clamp and ticks fired late. A plateau whose achieved arrival
rate is under 95% of the offered rate is generator-limited and counts for
nothing.

## 5. Finding the knee and the cliff

The search has three stages, all producing evidence for the same judge.

1. **Exploration.** Short plateaus at a spread of rates (for example 0.3
   to 0.7 per instance) until latency rises and then the backlog grows.
   This locates the band between the last flat plateau and the first
   collapsing one, and its stage timings say which component queues first.
2. **The result of record.** Ten-minute plateaus under three seeds at the
   rates that bracket the band, judged offline. Capacity is the highest
   plateau at which every series keeps up.
3. **The cliff.** One or two plateaus above the last keeping-up rate under
   the same seeds, so the capacity claim is a measured level with a failed
   level above it, the same standard as the rows below it.

On the reference server the curve has two distinct features. The **knee**
is where response time starts rising while completions still keep pace,
because some resource queues: the code agents' and data analysts' 95th
percentile rises first, since their steps run on the
executors' cores. The **cliff** is where arrivals outrun completions: the
compute-carrying archetypes stop completing inside the hold while the
executors' cores are full on both threads. The task, research, and
ingestion agents, whose steps hardly touch those cores, keep their
light-load latency even past the cliff, so the cliff shows in the backlog
and in the two heavy archetypes' curves rather than in every curve.
Between the two, a higher rate buys throughput at the cost of the heavy
agents' latency; nothing past the cliff is capacity.

## 6. Judging from evidence

Every run streams a per-unit **ledger** as it happens: each workflow's
type, admit time, completion time, outcome, offered rate, and stage sums;
every telemetry sample (host CPU, memory, per-family CPU attribution, the
executor spread, the generator's receipt); and a footer with the run's
counters. Units still in flight when the run stops are written as
censored rows (admitted, not finished). Weigh-in units admitted before
the generator starts carry no offered rate and are excluded from a
plateau's cohort.

The **plateau judge** (rule `plateau-1`) reads one held rate from the
fleet's ledgers, pooled:

- The cohort is every generator-admitted unit after a warm-up of 1.5 times
  the slowest completed latency (so the queue has filled to its steady
  depth) and before the last arrival.
- For each workflow type, completion latency p50 and p95 over the cohort;
  a unit still in flight when the run stops is censored and counts with
  its age so far, never as complete.
- Capacity holds when the backlog (arrivals to date minus completions to
  date) grows by at most five units or 5% of the cohort across the
  cohort's span, and the generator delivered at least 95% of the offered
  rate.
- Resident agents are rate x (mean time in system + 3 s think). The mean,
  not the median: in a tile that is half task agents the median unit is a
  10 s task agent, and a median-based figure would understate the agents
  alive by about four times.

A unit succeeds only if it completed inside its contract; a workflow
running longer than the patience ceiling (900 s under the modeled tier) is
a counted failure. The capacity of record is the highest plateau every
series keeps up at, with the range of achieved rates and of residency
across the three series reported. Rule changes are applied to history by
re-judging stored ledgers, never by re-running load.

### Per-unit stage accounting

Every ledger row carries the unit's own stage sums, gathered on the
executor that ran it: the model wait the stand-in modeled for each of its
calls, the retrieval pipeline and the reranker call inside it, any backoff
after a refusal, the sandboxed jobs' wall and CPU time by kind, and the
ingestion agent's embedding and indexing. A run's task is bound to an
accumulator when it starts, so the stages its parallel workers time land
on the same unit. `judge.py --stages` splits a plateau by archetype and
stage from those rows; the sums are resource time, not the critical path
(a research agent's three workers retrieve in parallel), so the reading is
across rates: the stage whose per-unit sum inflates as the rate rises is
where that archetype's slowdown lives, and what is left after the model
wait and the stages is the orchestration work the executors did for the
unit, which is where CPU starvation shows.

Enterprise tile, first series of the set of record, at capacity and one
rung above it (seconds per unit, medians):

| Archetype | Latency p50, 2.0 → 2.4 wf/s | Model wait (modeled) | Retrieval, sum (calls) | Rerank call, sum | Sandbox wall / CPU, sum (jobs) | Embedding | Remainder: orchestration on the executors |
|---|---|---|---|---|---|---|---|
| Task agent | 10.0 → 10.3 | 8.4 | – | – | – | – | 1.6 → 1.9 |
| Research agent | 33.5 → 34.2 | 28.7 | 1.15 → 1.45 (3) | 0.74 → 0.72 | – | – | 3.7 → 4.1 |
| Ingestion agent | 22.7 → 24.8 | 9.4 | – | – | 1.2 / 1.1 → 2.5 / 1.7 (1) | 10.2 → 10.9 | 1.9 → 1.6 |
| Data analyst | 104.2 → 159.3 | 27.9 | – | – | 72.1 / 71.6 → 126.6 / 100.4 (3) | – | 4.1 → 4.9 |
| Code agent | 132.9 → 236.1 | 26.8 | – | – | 102.3 / 102.0 → 204.4 / 151.2 (3) | – | 3.8 → 4.5 |

The model wait is identical at both rates to the tenth of a second, which
is the instrumentation's check on itself: it is modeled, not served. Every
archetype's remainder is 2 to 5 s at both rates, so the executors'
orchestration work is not what queues. What queues is the sandbox: at 2.4
the analyst's three jobs wait 127 s of wall for 100 s of CPU and the code
agent's three builds 204 s for 151 s, and the CPU itself rises by 40 to
50% because both threads of every application core are busy. The
reranker call (0.7 s across three calls at both rates) and the embedding
(10 to 11 s) are not the limit.

### Process families and the executor spread

Every sample attributes host CPU to process families: the instance's
control process, its executors, the sibling instances, the retrieval
tiers, the model stand-in, the databases (every PostgreSQL process on the
host), the sandboxed jobs (charged from the executors' reaped-children
time, since a short job never survives a process scan), and the
remainder. Each sample also carries the executors' per-process
distribution (min, median, max, in percent of one hardware thread), so an
uneven pool shows where a family total would hide it. At 2.0 workflows/s
of the enterprise tile in steady state, the sandboxed jobs are about
three quarters of the attributed CPU, the retrieval tiers about a ninth,
the four instances' orchestration processes about a ninth together, and
the databases and stand-ins under 1%; the 28 executors of an instance run
at 0 / 0.5 / 3 percent of a thread each, so the pool is even and idle,
waiting on jobs and on the modeled model. At 2.4 the sandbox family grows
by three quarters while everything else holds.

### The start-up transient

Every fleet run opens with a surge: the generator offers its full rate
from the first second while nothing completes for the first two minutes
(the code agents' and analysts' shape), so the in-flight count overshoots
its steady value, the extra jobs land on both threads of the application
cores, each job's CPU time rises, and the host reads high until the
overshoot drains. The drain takes longer as the rate nears the cliff,
because the excess capacity that drains it shrinks; past the cliff it
never drains. The judge's warm-up rule (units admitted before 1.5 times
the slowest completed latency are not in the cohort) and the ten-minute
hold are what keep it out of a number of record; a short check hold sits
inside it and must not be read as steady state.

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

A sampler reads `/proc` every two seconds and attributes CPU to groups:
the control process, the executors, the sibling instances, the model
stand-ins, the retrieval tiers (following process trees), the databases,
the sandboxed jobs, and a residual. Sandboxed jobs are charged from the
executors' reaped-children time (`cutime + cstime`), fleet-wide. Heavy
`/proc` work runs off the event loop so the sampler cannot stall the
generator.

Utilization is reported by **physical core**, sampled per hardware thread
with a core counted as busy as its busier sibling and averaged over the
steady window (`scripts/core_windows.py`), alongside the hardware-thread
figure a monitoring tool would show. The two differ because the reranker
deliberately leaves each of its cores' second threads idle, and because
single-threaded sandbox jobs occupy cores one thread at a time. The
reranker's attributed share is reserved capacity (its runtime threads
spin on their cores whatever the load); its consumed cost is demand over
its measured pair budget.

Host power management is part of the fingerprint: the reference server
manages CPU frequency in firmware with no OS governor exposed, idles cores
at 500 to 2,500 MHz, and reaches about 3.9 GHz under load, so light-load
latencies include a ramp of tens of milliseconds.

## 8. Cost laws: the sizing tools

Each component's cost is measured on the fleet so the capacity can
be rescaled to a different mix, depth, or job size.

- **Reranker.** The tier's ceiling is a pair budget: about 35 scored pairs
  per physical core per second in sixteen-pair calls of about 125 tokens,
  and about 55 in 128-pair calls, which batch better; sustainable rate
  scales inversely with candidates per call. A research agent's three
  calls at depth 128 cost about 7 core-s.
- **Embedding.** About 22 chunks per second per physical core (MiniLM-L6,
  FP32, 180-word chunks), so an ingestion agent's 480 chunks cost about
  22 core-s; the query embedder's single-query loads are negligible next
  to it, which is why ingestion has its own tier.
- **Sandbox.** The data job runs at about 0.43 core-s per million rows
  (17 core-s at 40 million, single-threaded, interpreter start included);
  the build-and-test step is about 33 core-s (the SQLite amalgamation is
  most of it); the parse is about 1 core-s per 100 pages. Under
  contention on both threads of a core each rises by a third to a half.
- **Orchestration.** A roughly fixed floor of about 17 hardware threads
  across the four instances (executors, control, stand-ins, databases),
  nearly independent of rate, plus a small per-workflow marginal cost (2
  to 5 s of executor time per workflow, section 6).
- **Per archetype** (each measured alone at two rates, floor cancelled):
  task agent 0.5 core-s per workflow, research agent 8.5 (about 7 of
  reranking), ingestion agent 24 (22 of embedding, 1 of parsing), data
  analyst 54 (three jobs of about 17), code agent 92 (three steps of
  about 31 to 39). Measured at light load, so lower bounds for the jobs
  under contention; in a mix, busy cores run at about 0.8 times the
  summed weights because sibling threads share physical cores.
- **Allocation.** Cores are divided so that the tiers keep headroom past
  the rate at which the executors' side runs out; with these tiles that is
  8 + 2 + 8 cores for the tiers and 46 for everything else, and the box is
  full at 2.0 to 2.6 workflows/s depending on the tile, whatever the
  split.

`scripts/archetype_costs.sh` runs an archetype alone at two rates and
`scripts/archetype_cost_summary.py` reads its cost; `scripts/cost_table.py`
produces the per-component table from any series;
`scripts/plateau_set_summary.py` produces the set summary;
`scripts/ratio_from_profile.py --mpstat` computes host work per generated
token from a plateau and its per-core samples.

## 9. Reproducibility record

Each result carries the seed, the software commit, the workload definition
(`config/capacity_scenarios.yaml` and the tile name), the
serving-tier parameters, rerank depth, sandbox isolation mode, the core
allocation (`allocation.env`), the process topology, the host profile, and
the ledger's SHA-256. Ledgers, judgments, and set summaries are committed
under `data/capacity/`. Two results compare only when their fingerprints
match.

The results of record are the three organisation tiles' sets
(section 11): enterprise `data/capacity/set-20260905-031403` (seeds 9801,
9901, 10001), engineering `set-20260905-060903` (9901, 10001, 10101),
analytics `set-20260905-090413` (10001, 10101, 10201); three seeds each,
ten-minute holds, run commits b86e16e and b68360b (the second a judge-only
change), evidence commits 8420fc7 and 7385a53. Their per-core samples are
`data/capacity/set-9800-mpstat.log`, `set-9900-mpstat.log`, and
`set-10000-mpstat.log` on the reference server.

## 10. Known limits

- The serving tier is modeled per payload and does not queue; inference-side
  saturation is outside the measurement, and the three serving parameters
  are an assumption recorded in the fingerprint. The recorded-profile path
  (section 6) replaces the formula with a named model's data.
- The vector index is modeled (15 ms); the keyword index, fusion, rerank,
  and packing are real. Retrieval quality is a diagnostic, not a gate.
- The tile weights, rerank depth, and job sizes are declared inputs. Their
  cost laws are published so results can be rescaled, but a published rate
  is for the declared tile.
- Runs cover a single host; multi-node coordination, failover, long soaks,
  and recovery after overload are not measured.
- Depths other than 128 and job sizes other than the declared ones are
  not measured.
- The GPU side of the ratio is a lab measurement of one accelerator and
  model, stated with its provenance, not a measurement made here.

## 11. The organisation mixes and the ratio

Host work per generated token is a property of the tile: what its agents
do between model calls, divided by the tokens those calls generate. The
ratio of orchestration servers to GPUs follows from it and from the
serving tier's tokens per second, so the three tiles give the ratio as a
function of what agents do rather than as one number.

### Results of record

Three sets, three seeds each, ten-minute holds: enterprise
`data/capacity/set-20260905-031403` (0.3 to 0.7 per instance, 1.2 to 2.8
workflows/s box-wide), engineering `set-20260905-060903` (same ladder),
analytics `set-20260905-090413` (0.5 to 1.1 per instance, 2.0 to 4.4
box-wide). Latencies are p50 / p95 in seconds, medians of three series;
host cores busy is the time-averaged per-core occupancy of the first
series (section 7); resident agents are rate x (mean time in system + 3 s
think), median of the three series. Zero failures through capacity in
every series.

**Enterprise.** Capacity 2.0 workflows/s with 87 resident agents; the
next rung falls behind.

| Offered (box-wide) | Code agent | Data analyst | Research | Ingestion | Task | Resident | Host cores busy | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1.2/s | 125 / 127 | 89 / 90 | 34 / 35 | 23 / 24 | 10 / 11 | 52 | 45% | keeps up |
| 1.6/s | 122 / 126 | 94 / 96 | 34 / 35 | 23 / 24 | 10 / 11 | 68 | 57% | keeps up |
| 2.0/s | 133 / 145 | 104 / 112 | 34 / 35 | 23 / 24 | 10 / 11 | 87 | 70% | keeps up: capacity |
| 2.4/s | 232 / 242 | 159 / 172 | 34 / 35 | 25 / 26 | 10 / 11 | — | 82% | past the cliff (executors 97%) |
| 2.8/s | — | — | 35 / 36 | 27 / 30 | 10 / 11 | — | 86% | past the cliff |

**Engineering.** Capacity 2.4 workflows/s with 102 resident agents; at
2.0 (88 resident) the heavy agents are still at their light-load latency.

| Offered (box-wide) | Code agent | Data analyst | Research | Task | Resident | Host cores busy | Verdict |
|---|---|---|---|---|---|---|---|
| 1.2/s | 122 / 125 | 87 / 88 | 34 / 35 | 10 / 11 | 52 | 43% | keeps up |
| 1.6/s | 118 / 121 | 89 / 91 | 34 / 35 | 10 / 11 | 67 | 55% | keeps up |
| 2.0/s | 131 / 141 | 101 / 108 | 34 / 35 | 10 / 11 | 88 | 64% | keeps up |
| 2.4/s | 189 / 199 | 129 / 135 | 34 / 35 | 10 / 11 | 102 | 74% | keeps up: capacity |
| 2.8/s | — | 225 / 234 | 35 / 36 | 10 / 11 | — | 77% | past the cliff (executors 100%) |

**Analytics.** Capacity 2.6 workflows/s with 106 resident agents; at 2.0
(72 resident) the analysts are still at their light-load latency.

| Offered (box-wide) | Data analyst | Research | Ingestion | Task | Resident | Host cores busy | Verdict |
|---|---|---|---|---|---|---|---|
| 2.0/s | 96 / 97 | 34 / 35 | 22 / 24 | 10 / 11 | 72 | 54% | keeps up |
| 2.6/s | 173 / 183 | 34 / 36 | 26 / 28 | 10 / 11 | 106 | 82% | keeps up: capacity |
| 3.2/s | — | 35 / 36 | 82 / 100 | 10 / 11 | — | 91% | past the cliff (executors 100%, ingest tier 94%) |
| 3.8/s | — | 35 / 37 | 114 / 217 | 10 / 11 | — | 92% | past the cliff |
| 4.4/s | — | 37 / 40 | 150 / 218 | 11 / 12 | — | 93% | past the cliff |

Per unit at 2.0 workflows/s (first series, medians of per-unit sums):
code agent 131 to 133 s, of which 100 to 102 s in three build steps and
27 s of model wait; data analyst 96 to 104 s, of which 63 to 72 s in
three jobs and 28 s of model wait; research agent 34 s with 1.2 to 1.4 s
of retrieval; ingestion agent 23 s with 1.2 s of parsing and 10 s of
embedding; task agent 10 s, of which 8.4 s is model wait.

### The ratio

GPUs per 64-core socket = 64,000 / (core-ms per generated token × generation tokens per second per GPU)

Host work per generated token is measured at 2.0 workflows/s in each
tile from the plateau's ledgers and per-core samples
(`scripts/ratio_from_profile.py --mpstat`): busy physical cores over the
steady window, divided by generated tokens per second. It is a property
of the tile, not of the load: across each ladder it moves within a few
core-ms.

| Tile | Measured at | Busy cores | Generated tokens/s | Core-ms per token | Across the ladder |
|---|---|---|---|---|---|
| Enterprise | 2.0 wf/s | 44.7 | 2,076 | 21.5 | 21.5 to 22.7 |
| Engineering | 2.0 wf/s | 41.1 | 2,058 | 20.0 | 19.0 to 22.4 |
| Analytics | 2.0 wf/s | 34.4 | 2,110 | 16.3 | 16.3 to 19.8 |

The GPU side is not measured here. The reference band is a lab
measurement of one RTX PRO 6000 serving a 35B mixture-of-experts model
with 3B active in FP8 (window average 1,300 generation tokens/s over a
draining fleet; peaks 2,400 to 3,800), stated with that provenance; the
single-GPU recording that replaces it with our own measurement on this
workload's calls is described in section 6 and is an enhancement, not
part of this result.

| Generation tokens/s per GPU | Enterprise | Engineering | Analytics | Twelve task agents (estimate) |
|---|---|---|---|---|
| 1,300 | 1 : 2.3 | 1 : 2.5 | 1 : 3.0 | 1 : 55 |
| 2,400 | 1 : 1.2 | 1 : 1.3 | 1 : 1.6 | 1 : 30 |
| 3,800 | 1 : 0.8 | 1 : 0.8 | 1 : 1.0 | 1 : 19 |

All three organisation tiles cross 1:1 inside the band: about one
orchestration socket per GPU at the lab's conservative peak, and two to
three GPUs per socket at its window average. The last column is the
support-desk case, a tile of task agents alone, at the task agent's
measured weight (0.9 core-ms per token); it is an estimate from the
catalog, not a measured set, and shows what agents that only wait on the
model do to the ratio. What moves the ratio is the host work per token,
which the tile sets, and the tokens per GPU, which the model and
accelerator set; a slower serving tier changes residency and the
response curve, never the ratio.

### Where small-model inference runs is a sensitivity, not the result

Validation is the tempting lever, and the benchmark keeps it off the
host. Most validations in these workflows are judgments over a context
rather than text (grounding, schema, test result, policy), and an encoder
model could answer each in one forward pass on the host, the same class
of work the host already does for reranking. But a full-context check
costs about 0.6 to 1 core-s on the host against about 5 ms on a GPU, and
seven per workflow would add more host work than the research agent's
whole cost; placing them on the host would move the ratio by that amount,
and a reader would be right to say the mechanism is inference the GPU
does a hundred times cheaper. Validations therefore run as calls to the
serving tier in every published number, the ratio moves only through
work nobody can call a lever (builds, data jobs, embedding, reranking),
and host-side encoder validation is at most a published sensitivity with
its per-check cost stated for both placements. No generative judging
runs on the host in any variant.
