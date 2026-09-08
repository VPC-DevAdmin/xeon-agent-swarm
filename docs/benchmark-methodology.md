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
  measured: the median of the fleet's in-flight counts over the steady
  window. Little's law (rate x mean time in system) is the check on it;
  near the cliff the law understates residency, because the slowest units
  have not completed when the hold ends and drop out of the mean, and the
  samples do not.

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
them: sizes are declared parameters and are stated with every result.
What each archetype generates per workflow is calibrated: its model calls
were recorded from a traced run, replayed against a named model, and the
recorded token counts answer the same call positions in every measured
run (section 6), so generated tokens are the model's, not a formula's.

| Archetype | Declared size | Workers | Model calls (planner and workers + judgments) | Lookups | Host work per workflow, stand-alone | Output tokens per workflow, judgments included (gpt-oss-20b, low reasoning) | Core-ms per token, stand-alone |
|---|---|---|---|---|---|---|---|
| Task agent | one ticket: one knowledge-base lookup, one record, the worker's answer handed back | 1 | 4 + 1 | 1 at depth 32 | about 1 core-s | about 1,400 (v2.3 calibration in progress; 2,440 in the v2.2 shape) | 0.7 |
| Research agent | 90 source pages fetched and parsed, nine retrievals at rerank depth 128 | 3 | 16 + 4 | 9 at depth 128 | about 25 core-s | 15,500 | 1.6 |
| Ingestion agent | 50 PDF pages rendered, OCR'd, redacted, chunked, embedded and indexed | 1 | 5 + 2 | none | about 100 core-s | 3,470 | 29 |
| Data analyst | three jobs over 100 million rows, the second over two periods | 3 | 13 + 4 | 2 at depth 64 | about 190 core-s | 12,940 | 15 |
| Code agent | setup, CI and verification over Lua 5.4.7 and SQLite 3.50.4 | 3 | 13 + 4 | 3 at depth 64 | about 150 core-s | 12,400 | 12 |

Host work is the stand-alone sum of each archetype's steps (the cost laws
of section 8); in a mix, busy cores run at about 0.8 times the summed
weights because sibling threads share physical cores. Output tokens are
the completed units' own at the capacity rung of the enterprise set of
record, three seeds, plus the model-based judgments' outputs (one per
worker and one on the synthesis, 66 to 105 tokens each), which the
run records keep in a separate validation counter. Every archetype that acts looks things up
first, and the lookup is host work inside the step, never a model turn:
the query embedder, the index and the reranker are the server's own.

- **Task agent**: a trigger, triage, or routing agent: born, does one
  thing, dies. The planner delegates the ticket in one turn; one worker
  reads it, looks it up in the knowledge base (one retrieval, 32
  candidates reranked), files a durable record and writes the reply;
  the reply is judged once and handed back as the deliverable in a
  short closing turn with only the mechanical check on it (the
  workflow declares `grade_synthesis: false`). That is the shape of a
  routed ticket agent: the planner routes, the worker answers, nothing
  re-drafts the answer. It is most of any deployment by count. Its
  tokens are the model's for this shape: about 350 in the planner's
  delegation, 960 in the worker's two turns, 70 in the judgment and a
  short handoff, about 1,400 in all, against 2,440 when the planner
  re-drafted the answer and a second judgment graded it (the v2.2
  shape, section 12). A smaller or non-reasoning model emits fewer
  (section 11), and the record keeps one model for every archetype.
  Latency about 20 s at every load, almost all of it model wait.
- **Research agent**: three workers each fetch and parse 30 source pages
  in the sandbox (boilerplate removal and main-text extraction over real
  HTML), retrieve three times over the corpus with the cross-encoder
  scoring 128 candidates per query, draft a section, and a synthesis
  step assembles the brief. Nine depth-128 queries are about 23 core-s
  of reranking; the fetch is under 2. Latency about 150 s, mostly the
  sixteen model calls' wait.
- **Ingestion agent**: one worker takes a seeded selection of 50 PDF
  pages through document intake in the sandbox: each page is rendered
  and read by Tesseract OCR (about 1.4 core-s per legible page), the
  text is scanned for personal data and redacted (e-mail addresses,
  phone numbers, card numbers), chunked at 180 words with a 30-word
  overlap and de-duplicated; the executor then embeds the chunks on the
  ingest embedder and indexes them, and the check query is the
  verifier. About 100 core-s, most of it OCR. Latency about 125 s.
  OCR on the host is the on-premises choice, where documents do not
  leave the building; it costs what Tesseract costs, about 1.4 core-s
  per page, and is not inflated. A deployment with a GPU OCR model
  moves that work off the host: at one ingestion agent in twelve that
  is about 7 of the tile's 58 core-s per workflow, and the tile's host
  work per token goes from 9.0 to about 8.2 core-ms.
- **Data analyst**: three workers each run a sandboxed job over 100
  million rows of payment events, a week's worth: profile, rank and
  explain, report. The analysis worker's job runs over two periods, this
  week and the previous, and reports the change period over period. The
  research and analysis workers pull the schema and metric definitions
  first (one retrieval each, 64 candidates). About 47 core-s per job,
  95 for the two-period job. Latency about 340 s at light load; that is
  its shape, not a queue.
- **Code agent**: three workers take a real working tree, Lua 5.4.7 and
  the SQLite 3.50.4 amalgamation, through a continuous-integration
  change: a setup step (fresh tree, optimised build of both, both
  suites), a CI step (instrumented build under the undefined-behaviour
  sanitizer, both suites, static analysis of every Lua source with the
  compiler's analyzer), and a verification step (touch the changed
  sources, incremental rebuild, suites, a lint pass). Each worker
  searches the codebase and its documentation first (one retrieval, 64
  candidates). About 34, 74 and 40 core-s; nothing in the tree is
  generated. Its thirteen calls are coarse by design: a deployed coding
  agent takes many more, smaller turns (read, search, edit), which
  changes what it asks of the serving tier's prefill far more than its
  generated tokens or its host work, and is accounted for on the
  serving side rather than by adding turns here. Latency about 255 s
  at light load.

Why these five: each is a role a reader recognises and would deploy, and
each carries a different kind of host work in a different amount, so the
cost of retrieval, OCR, embedding, data jobs, and builds is each
identifiable from the data, and the per-agent lifecycle cost is carried
alone by the task agent. Five roles is already a lot for a reader;
variants of a role at other sizes are not archetypes.

### The tiles

The unit of load is a **tile** of twelve workflow arrivals. Three tiles
describe three organisations; small agents dominate by count, as they do
in a deployment, and the compute-carrying archetypes set the host work
per token. The enterprise tile is the tile of record. The engineering
and analytics tiles are declared and have not been measured on the
archetypes of record; their earlier measurements are superseded
(section 12).

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

### The serving tier is modeled per call, with calibrated tokens

No model call is instantaneous. A deterministic stand-in answers every call
through the production request path and waits as a remote serving tier
would: 500 ms to first token, plus output tokens at 100 per second, plus
input tokens at 8,000 per second, with 20% seeded jitter. The token
counts it waits for and reports are calibrated, not estimated: every
call position in every workflow (archetype, worker role, turn) was
recorded from a traced run, replayed against gpt-oss-20b at low
reasoning effort through a serving endpoint, and the recorded prompt
and completion sizes answer the same position in a measured run,
chosen by the unit's seed (`CAPACITY_SERVING_PROFILE`, section 6). A
worker's turns are matched by shape, a tool-call turn or a draft, so a
step added after the calibration still answers with calibrated sizes;
a changed shape (the task agent's handoff, v2.3) is recaptured and
replayed on its own and merged into the profile of record.
The three timing parameters and the profile are part of the machine
fingerprint. Each call re-sends its whole context and is charged
prefill for all of it; a serving tier with prompt caching would charge
less, and the benchmark keeps the pessimistic accounting because the
host's cost, the quantity measured, does not change with the tier's
cache policy. Validations are calls to the serving tier like any other;
no generative inference runs on the host.

### Retrieval is real work on the server

Every retrieval embeds the query on the query embedder, runs BM25
keyword search over a seeded 120,000-passage store (SQLite FTS5, 2,000
topics, built once per server), fuses that ranking by reciprocal rank
with the answer of a vector index (modeled as a 15 ms off-server call,
because a large vector database is its own system in any deployment),
prefilters to the declared depth with a keyword scorer, scores those
candidates with an INT8 cross-encoder (ms-marco-MiniLM-L-6-v2) on the
processor's AMX units, and packs the winning passages into the worker's
context with `[chunk-N]` citations. Workers cite the passage ids they
were given, so grounding is checkable by construction.

Retrieval is host work, never a model turn. The research agent's
retrieval step is a tool call that scores three queries; every other
lookup rides the step the agent was already taking: the task agent
looks the ticket up as it files its record, the code agent searches the
codebase and its documentation as it starts each CI step, the analyst
pulls schema and definitions as it starts each of its first two jobs.
The lookup runs on the retrieval tiers before the step's own work and
its packed passages (1,500 words per lookup, 6,000 for the research
step) return with the step's result. Depths (32, 64, 128) are declared
per archetype and ride the fingerprint; the cost law is in section 8.
Retrieval quality is reported as an in-topic fraction and never judged:
capacity is invariant to relevance, since reranking 128 relevant
passages costs what reranking 128 irrelevant ones costs.

### Execution is a real, bounded sandbox

A worker that executes calls a tool that runs one job in a fresh, isolated
interpreter (`python -I -S`) under CPU-time, address-space, and file-size
limits, with no network (a network namespace via `sudo unshare -n`; the
isolation mode is fingerprinted), single-threaded math, seeded
deterministic inputs, and a few hundred characters of results returned
into the worker's context. Four kinds of job exist, one per archetype
that executes:

- The **data job** (data analyst) is the shape of an analyst's tool run
  over a week of payment events: generate the 100-million-row event
  table, join it to a merchant table, bucket by merchant and minute,
  sort-based per-merchant percentiles, a rolling load window, tail
  quantiles, z-scored anomaly ranking against a category baseline, and a
  second pass over the flagged merchants. About 47 core-s stand-alone.
  The analysis worker's job runs the same pass over two periods (this
  week's seed and the previous week's) and reports the deltas in total
  value, tail quantile, outliers and merchant means, the way a weekly
  report compares to the last one. About 95 core-s.
- The **CI job** (code agent) is one step of a continuous-integration
  change over the vendored Lua 5.4.7 tree and the SQLite 3.50.4
  amalgamation. *Setup* copies a fresh tree and builds both with gcc
  -O2, Lua through its own Makefile and SQLite from `sqlite3.c` with its
  shell, and runs Lua's own test suite in its portable mode and an
  integration script against the built engine (schema, 300,000 inserted
  rows, an index, aggregates, an integrity check): about 34 core-s.
  *CI* rebuilds both under the undefined-behaviour sanitizer
  (`-fsanitize=undefined -O1 -g`), runs both suites instrumented, and
  runs the compiler's static analyzer (`gcc -fanalyzer`) over every Lua
  source file: about 74 core-s. *Verify* touches the changed sources
  (`lvm.c`, `lapi.c`), rebuilds incrementally, runs both suites, and
  runs a lint pass (`-Wall -Wextra -fsyntax-only`) over the tree: about
  40 core-s. The suites' result is the verifier. The address sanitizer
  is not used because its shadow memory does not fit the sandbox's
  address-space cap.
- The **intake job** (ingestion agent) opens a seeded selection of PDF
  documents and takes 50 pages through document intake: each page is
  rendered at two times scale (pypdfium2) and read by Tesseract OCR in
  page-segmentation mode 6 with one thread (about 1.4 core-s per
  legible page), the text is scanned with personal-data patterns
  (e-mail addresses, phone numbers, card numbers) and redacted, then
  normalized, split into 180-word chunks with a 30-word overlap and
  de-duplicated; the chunks go back to the executor, which embeds them
  on the ingest embedder and indexes them for search. About 75 core-s
  of OCR and a few of rendering and redaction; the documents are
  generated with a legible layout so the OCR reads real text rather
  than noise, and OCR text quality is reported, not judged.
- The **fetch job** (research agent) takes a worker's share of 30 source
  pages (real HTML with navigation, footers and scripts) through
  boilerplate removal and main-text extraction (trafilatura), and
  returns the word count and leads. About 0.5 core-s.

Job sizes are declared parameters (the row count, the vendored project,
`SCAN_PAGES`, `FETCH_PAGES`) and are stated with the result. It is
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
other. The allocation of record for the enterprise tile is 8 cores for
the reranker (two processes of four inference threads each), 2 for the
query embedder, 3 for the ingest embedder, and the remaining 51 for the
four instances, their executors, stand-ins, databases, and sandboxed
jobs, which are pinned there so nothing shares AMX units with the tiers.
Provisioning selects it with `RERANK_PHYS_CORES=8 RERANK_WORKERS=2
RERANK_THREADS=4 EMBED_PHYS_CORES=2 INGEST_EMBED_PHYS_CORES=3`, and it
rides the run fingerprint (`allocation.env`). The allocation is sized
from the lookups the tile makes (section 8): at capacity the tile
scores about 140 candidate pairs a second, 2.8 cores of reranking, and
two processes keep a depth-128 query's wait short at that load.
Earlier allocations are listed with the superseded results in section
12. Pinning is by cpuset,
not quota: a CPU quota on a 128-thread host lets a many-threaded process
burn its allowance in milliseconds and sleep for the rest of the period.

The allocation is set from measured costs so that the tiers keep headroom
past the rate at which the executors' side runs out, which is what makes
the server, rather than an allocation, the limit: a tier sized to
saturate at the target queues at the target and shows a knee the cores do
not have, and a tier sized generously starves the other side. At the
enterprise tile's capacity the application pool's 51 cores are about
96% occupied over the steady window (time-averaged per core, busier
sibling) and past the cliff 99 to 100%, full on both threads of most
cores, while the retrieval tiers stay under half occupied at every rate. The cliff is the application
pool's: builds, OCR and data jobs queue for cores, and the code agent's
and analyst's latencies double while the task and research agents, whose
time is model wait, do not move. The sizing arithmetic is in section 8.

The reranker tier is pinned server processes, each on its own whole
cores with its own queue; every executor rotates its calls across them
per call, moving a refused call to the next process before it backs off. The shape matters as much as the core count:
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
record uses 25-minute holds under three seeds that differ by 100, long
enough for the nine-minute analysts to reach steady state. Rates are
offered per instance (four instances, so 0.21 per instance is 0.84
box-wide) and may be fractional.

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
- Resident agents are measured: the median over the cohort span of the
  instances' in-flight counts summed per two-second bucket. Little's law,
  rate x (mean time in system + 3 s think), is reported beside it as the
  check; it agrees within ten percent below the knee and understates near
  the cliff, where the slowest units are censored out of the mean.

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

Enterprise tile, the record's second series (seed 11501), below capacity
and at it (seconds per unit, medians):

| Archetype | Latency p50, 0.72 → 0.84 wf/s | Model wait (modeled) | Retrieval, sum (calls) | Rerank call, sum | Sandbox wall / CPU, sum (jobs) | Embedding | Remainder: orchestration on the executors |
|---|---|---|---|---|---|---|---|
| Task agent | 30.4 → 30.3 | 26.8 | 0.44 → 0.45 (1) | 0.32 → 0.33 | – | – | 3.1 → 3.1 |
| Research agent | 180.7 → 181.6 | 167.6 | 4.0 → 4.3 (9) | 2.8 → 3.1 | 2.1 / 1.7 → 2.8 / 2.4 (3, fetch) | – | 7.1 → 6.9 |
| Ingestion agent | 127.0 → 151.8 | 40.0 | – | – | 79.7 / 79.6 → 105.5 / 105.3 (1) | 5.4 → 5.8 | 1.9 → 0.5 |
| Data analyst | 374.6 → 524.6 | 137.8 | 0.88 → 0.88 (2) | 0.58 → 0.59 | 227.9 / 170.3 → 385.5 / 284.7 (3) | – | 8.0 → 0.3 |
| Code agent | 276.4 → 342.6 | 125.8 | 1.29 → 1.39 (3) | 0.87 → 0.96 | 140.6 / 140.3 → 207.0 / 206.6 (3) | – | 8.5 → 8.2 |

The model wait is identical at both rates to the tenth of a second, which
is the instrumentation's check on itself: it is modeled, not served. Every
archetype's remainder is a few seconds at both rates, so the executors'
orchestration work is not what queues. What queues is the sandbox: at 0.84
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
the slowest completed latency are not in the cohort) and the 25-minute
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
steady window of the hold, from 600 s after the rung starts to 1,500 s
(`scripts/core_windows.py`, `scripts/ratio_from_profile.py --window`),
alongside the hardware-thread figure a monitoring tool would show. The
window starts late because the slowest archetype takes about nine
minutes, so the population on the server is still climbing through the
first third of a 25-minute hold; an earlier window read the transient
and understated busy cores by about a tenth. The two differ because the reranker
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

- **Reranker.** The tier's cost is a pair budget: about 20 ms of core
  time per candidate pair scored (the slope of the reranker's occupancy
  against its load across the enterprise ladder, 50 pairs per core per
  second), so a query costs its depth: 0.65 core-s at depth 32, 1.3 at
  64, 2.6 at 128. The tile's lookups (six at 32, ten at 64, nine at 128
  per twelve workflows) are about 1,980 pairs, 3.3 core-s per workflow.
  A query's wait is its depth divided by the process's threads: about
  0.65 s at depth 128 on four threads, so two processes keep the wait
  near that at two queries a second.
- **Embedding.** About 22 chunks per second per physical core (MiniLM-L6,
  FP32, 180-word chunks), so an ingestion agent's chunks from 50 pages
  cost about 12 core-s; a query embedding is about 0.2 core-s including
  the call, about 5 core-s per twelve workflows.
- **Sandbox.** The data job runs at about 0.475 core-s per million rows
  (47 core-s at 100 million, single-threaded, interpreter start
  included) and its two-period form at twice that; the CI steps are
  about 34 (setup), 74 (CI with the sanitizer build and the analyzer)
  and 40 (verify) core-s; OCR is about 1.4 core-s per legible page plus
  rendering and redaction, about 80 core-s per 50 pages; the fetch is
  about 0.5 core-s per 30 pages. Under contention on both threads of a
  core each rises by a third to a half.
- **Orchestration.** A roughly fixed floor of about 17 hardware threads
  across the four instances (executors, control, stand-ins, databases),
  nearly independent of rate, plus a small per-workflow marginal cost (2
  to 5 s of executor time per workflow, section 6).
- **Per archetype** (stand-alone sums of the laws above): task agent
  about 1 core-s per workflow, research agent about 25 (23 of
  reranking), ingestion agent about 100 (most of it OCR, 12 of
  embedding), data analyst about 190 (jobs of 47, 95 and 47, plus two
  lookups), code agent about 150 (steps of 34, 74 and 40, plus three
  lookups). In a mix, busy cores run at about 0.8 times the summed
  weights because sibling threads share physical cores: the enterprise
  tile sums to about 68 core-s per workflow and measures 54 to 57.
- **Allocation.** Cores are divided so that the tiers keep headroom past
  the rate at which the executors' side runs out; for the enterprise tile
  that is 8 + 2 + 3 cores for the tiers and 51 for everything else. A
  core moved from a tier with headroom to the application pool buys
  capacity at the same host work per token, and a tier sized below its
  pair budget turns every lookup into a queue wait.

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

The results of record are the enterprise tile's sets (section 11):
`data/capacity/set-20260908-025329` (seeds 11401, 11501, 11601; 0.84
and 0.96 workflows/s) with its lower rung `set-20260908-053752` (same seeds,
0.72), its midpoint rung `set-20260908-115604` (same seeds, 0.90) and its
residency photographs `photo-11401-20260908-070145`, `photo-11501-20260908-075234`, `photo-11601-20260908-084324` (152 sessions). Three
seeds each, 25-minute holds (30 for the photographs); run commit
ebaf94f, evidence commits 5add991, bce6f26, 3df029a and 325d7d7. Their
per-core samples are `data/capacity/set-11400-mpstat.log`,
`set-11400c-mpstat.log` and `set-11400d-mpstat.log` on the reference
server. Earlier sets are superseded and listed in
section 12. The serving profile of record is
`data/capacity/serving/gptoss20b-low-faithful/calls.jsonl`, recorded
from the query set `data/capacity/queryset/enterprise.jsonl`; the
sensitivity profiles (gpt-oss-120b, Qwen3.8 Flash without thinking,
gpt-oss-20b at medium reasoning) sit beside it.

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
- Depths and job sizes other than the declared ones are not measured.
- The GPU side of the ratio is a published measurement of one
  accelerator and model, context-adjusted and cited (section 11), not a
  measurement made here.

## 11. The organisation mixes and the ratio

Host work per generated token is a property of the tile: what its agents
do between model calls, divided by the tokens those calls generate. The
ratio of orchestration servers to GPUs follows from it and from the
serving tier's tokens per second, so the result is the ratio as a
function of what agents do rather than as one number; the enterprise
tile is the tile of record, and the archetype table of section 2 gives
the constant of any other tile before it is measured.

### Results of record

*The v2.3 set (the task agent's handoff shape, section 2) is being
measured; the figures below are the v2.2 set's and are replaced when it
lands. The definitions, windows and weighting rules are the ones the v2.3
figures will use.*

One set, three seeds, 25-minute holds: enterprise
`data/capacity/set-20260908-025329` (0.21 and 0.24 per instance, 0.84
and 0.96 workflows/s box-wide) with its lower rung `set-20260908-053752`
(0.18, 0.72) and its midpoint rung `set-20260908-115604` (0.225, 0.90), on
the allocation of record, the archetypes of section 2
and the calibrated gpt-oss-20b profile. Latencies are p50 / p95 in
seconds, medians of three series; host cores busy is the median of the
three series' time-averaged per-core occupancy over the steady window
(section 7); resident
agents are measured from the fleet's in-flight samples. Zero failures in
every series at every rung, including past the cliff.

**Enterprise.** Capacity 0.84 workflows/s with 152 resident agents; 0.90
falls behind in every seed, and 0.96 further.

| Offered (box-wide) | Code agent | Data analyst | Research | Ingestion | Task | Resident | Backlog over the hold (three seeds) | Host cores busy | Application pool | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.72/s | 275 / 293 | 374 / 392 | 182 / 199 | 125 / 144 | 30 / 35 | 107 | -14, -6, -4 | 70% (45 cores) | 81% | keeps up |
| 0.84/s | 343 / 369 | 527 / 563 | 184 / 202 | 152 / 169 | 30 / 35 | 152 | +18, +18, +23 | 83% (53 cores) | 96% | keeps up: capacity |
| 0.90/s | 410 / 452 | 654 / 673 | 186 / 206 | 188 / 211 | 30 / 35 | 181 and climbing | +51, +50, +50 | 86% (55 cores) | 99% | falls behind |
| 0.96/s | 501 / 546 | none completed inside the hold | 188 / 210 | 226 / 256 | 31 / 35 | 215 and climbing | +85, +84, +83 | 87% (56 cores) | 100% | past the cliff |

The response curve is the three sandboxed archetypes': from 0.72 to
0.84 the code agent, the analyst and the ingestion agent lengthen as
their jobs queue for application cores; at 0.90 the backlog grows by
fifty over the hold with every heavy archetype a quarter slower again,
and past the cliff the code agent's median passes 500 s, the analysts
stop completing inside the hold, and ingestion's median rises by half; the research and task
agents, whose time is model wait, hold 184 and 30 s at every rate
including past the cliff. The cliff is the application pool's, at 96%
occupancy at capacity with both threads of most cores busy: under that
contention each job's CPU time rises by a third to a half over its
stand-alone cost (the analyst's three jobs take 285 core-s at capacity
against 190 stand-alone, the code agent's three steps 207 against 150),
which is what makes the cliff sharp. The retrieval tiers stay at 40%
(reranker), 18% (query embedder) and 22% (ingest embedder) at capacity
and barely move past the cliff.

Per unit at capacity (seed 11501, medians of per-unit sums): data
analyst 525 s, of which 386 s in three jobs (285 core-s), 138 s of
model wait and 0.9 s in two lookups; code agent 343 s, of which 207 s
in three CI steps, 125 s of model wait and 1.4 s in three lookups;
research agent 182 s, of which 167 s of model wait, 4.3 s in nine
retrievals (3.1 s in the reranker) and 2.8 s of fetching; ingestion
agent 152 s, of which 106 s of OCR intake, 40 s of model wait and 5.8 s
of embedding; task agent 30 s, of which 26.7 s is model wait and 0.45 s
the knowledge-base lookup.

### The residency photograph

Residency in the tables above is derived from an open-loop run: agents
arrive on a schedule and the count on the server follows. The photograph
is the closed-loop confirmation: the fleet holds a fixed number of
sessions, each of which submits a workflow, waits for it, thinks three
seconds and submits the next, drawing the next slot of the tile's mix
each time so the completed mix is the declared mix
(`scripts/residency_photo.sh`, `scripts/residency_summary.py`). The
server then shows what it sustains with that many agents on it.

| Sessions held | In flight, measured | Completions / s | Code agent p50 / p95 | Data analyst | Research / ingestion / task | Drift, first to second half of the hold | Host threads busy | Host memory |
|---|---|---|---|---|---|---|---|---|
| 152 | 150 | 0.80 to 0.81 | 337 / 365 s | 526 / 560 s | 183 / 147 / 30 s | within 2% for every type (ingestion +5% in one seed) | 70% | 390 GB |

Three seeds, 30-minute holds, zero failures in 4,254 workflows; the
seeds agree to the second decimal on throughput and to within a few
seconds on every latency. Little's law closes it: 0.81 a second times
189 seconds is 153 against 152 held and 150 measured in flight. The
152-session photograph is the 0.84 workflows/s point seen from the
other side: the same server, holding 150 agents in flight, completes
0.8 workflows a second at the latencies the open-loop ladder measured
at its capacity rung, and holds them for half an hour without drift.
"This server carries about 150 working agents" is therefore a
measurement, not a derivation. The memory figure is real too: the
analysts' 100-million-row jobs hold about 40 GB each while they run,
and a dozen of them are in flight at once.

### The ratio

One server, as configured and measured, generates a certain number of
tokens per second through its agents' model calls; a GPU of a given
class generates a certain number per second. The ratio is the quotient:

GPUs one server keeps busy = generated tokens per second, measured on the server / generation tokens per second per GPU

Generated tokens per second are the achieved rate times the declared
mix's output per workflow: each archetype's output per completed
workflow from the run records (its planner and worker calls) plus its
model-based judgments' outputs, weighted by the tile. At a plateau that
keeps up, completions per archetype equal arrivals per archetype, so
this is the steady-state rate; the completed cohort of a finite hold
over-represents the short workflows (task agents are 54 to 60% of
completions against 50% of arrivals, because the long workflows are
still in flight when the hold ends), so a completion-weighted average
understates the mix by about a tenth and is not used. The figure is the
whole server's, tiers and headroom included, with no scaling to busy
cores. Host work per generated token, busy physical cores over the
steady window divided by generated tokens per second
(`scripts/ratio_from_profile.py --mpstat`), is reported beside it as the
constant that explains it: it is a property of the tile, not of the load,
and moves within a core-ms across the passing rungs.

| Enterprise tile | Offered | Generated tokens/s | Busy cores | Core-ms per token | GPUs the server keeps busy at 3,500 tokens/s per GPU (record) | at 2,400 / 4,378 |
|---|---|---|---|---|---|---|
| below capacity | 0.72 wf/s | 5,080 | 44.8 | 8.8 | 1.45 | 2.12 / 1.16 |
| **at capacity** | **0.84 wf/s** | **5,920** | **53.1** | **9.0** | **1.69** | 2.47 / 1.35 |
| first rung past capacity | 0.90 wf/s | 6,350 | 55.0 | 8.7 | 1.81 | 2.65 / 1.45 |
| past the cliff | 0.96 wf/s | 6,770 | 55.6 | 8.2 | 1.93 | 2.82 / 1.55 |

Output tokens per workflow on the declared mix are 7,030 (task 2,440,
code 12,400, analyst 12,940, research 15,500, ingestion 3,470, judgments
included, the same to within 20 tokens at every rung and seed). The
constant is 8.8 to 9.2 core-ms per token on the passing rungs across
all three seeds; past the cliff it reads lower only because the pool is
saturated and arrivals' tokens outrun the host work being done.

The GPU side is not measured here. The record rate is 3,565 output
tokens/s per GPU: the published output-only serving rate of the model
of record on one RTX PRO 6000, less 5%. Its derivation: Database Mart
measured gpt-oss-20b under vLLM on one RTX PRO 6000 Blackwell Server
Edition at 50 concurrent requests, 100 input and 600 output tokens per
request, at 3,752.9 output tokens/s (its table also reports 4,378 total
tokens/s, input and output together, which is not the figure to use).
The calls recorded from this workload (section 6) carry a mean of 3,100
prompt tokens (median 1,700, p90 6,900) and generate a mean of 1,000
each. Prefix caching is assumed: agent loops grow their context call by
call, so with the caching every serving stack provides only each call's
new tokens are prefilled, and the published short-prompt rate is the
right basis for the decode work. The 5% haircut covers what caching
does not: each call's own new tokens are several hundred rather than
the benchmark's 100, and the workers' prompts share less prefix than a
chat session's do. Millstone AI's context sweep of the same model on the
same card, which prefills the whole context on every call, shows about
30% lost from 1K to 8K context; that is the bound without caching, and
the single-GPU replay of these calls with caching on and off (section 6)
is what would replace both the assumption and the haircut. The source
describes the model's precision inconsistently (4-bit in its model list,
8-bit in its results table); gpt-oss-20b is distributed in MXFP4, and the
rate is cited as published. The band around the record: 2,400 is the
conservative rate at which the ratio was first stated; 3,753 is the
published output rate unadjusted; NVIDIA's TensorRT-LLM tables put a 30B
mixture-of-experts model with 3B active at 9,938 output tokens/s per GPU
in FP4 at 1,000 input and 1,000 output tokens, and CloudRift measured the
same model class at about 8,400 under vLLM at 400 concurrent requests,
which bounds what a different model choice does on the same card.
Low-concurrency serving sits far below all of these: Millstone's own peak
was 642 tokens/s because it never ran more than five requests at once,
and the Metrum agent-density runs, which served the model on the same
server as the agents, produced about 390 tokens/s per GPU with the GPUs
46% busy and nothing queued; against that figure a server generating
5,000 tokens/s would keep about thirteen GPUs busy, which is the case for
a serving tier of its own rather than a limit of the card.

References for the serving rate:

- Database Mart, "Pro 6000 vLLM Inference Benchmark: LLM Throughput and Latency Analysis", August 2026: gpt-oss-20b under vLLM on one RTX PRO 6000 Blackwell Server Edition, 50 concurrent requests, 100 input and 600 output tokens per request: 3,752.9 output tokens/s (4,378.4 total tokens/s including input); gpt-oss-120b, same conditions, 1,524 output tokens/s (1,779 total). The source's precision labels are inconsistent (4-bit in its model list, 8-bit in its table). https://www.databasemart.com/blog/vllm-gpu-benchmark-pro6000
- Millstone AI, "gpt-oss-20b: Performance Analysis on 1x RTX Pro 6000 Blackwell", 28 January 2026: MXFP4, vLLM, one to five concurrent requests, context 1K to 128K; 642 tokens/s at five requests and 1K context, 222 at 32K; the context sweep behind the haircut. https://cdn.millstoneai.cloud/benchmarks/gpt-oss-20b-mxfp4-1x-rtx-pro-6000-blackwell/gpt-oss-20b-mxfp4-1x-rtx-pro-6000-blackwell.pdf
- NVIDIA, TensorRT-LLM performance overview, RTX 6000 Pro Blackwell Server Edition tables, updated 27 August 2026: Qwen3 30B A3B in FP4 at 1,000 input and 1,000 output tokens, 9,938 output tokens/s per GPU; Llama 3.3 70B in FP4, 1,724. https://nvidia.github.io/TensorRT-LLM/latest/developer-guide/perf-overview.html
- CloudRift, GPU benchmarks for LLM inference, October and November 2025: Qwen3-Coder-30B-A3B AWQ under vLLM at 400 concurrent requests, about 8,400 output tokens/s on one RTX PRO 6000; GLM-4.5-Air AWQ at 256 to 512 concurrent requests, 3,140. https://www.cloudrift.ai/gpu-benchmarks
- Metrum AI, agent-density runs on a PowerEdge R770 with the model served on the same server: about 390 generation tokens/s per GPU at the density boundary with the GPUs 46% busy (the reports supplied with this project).

At capacity one server, 83% busy, keeps 1.7 GPUs busy at the record
rate and 2.5 at the conservative 2,400. Stated per core, 9.0 core-ms
per token at 3,500 tokens/s is 31 cores per GPU, so a 64-core socket
run flat out pairs with two GPUs: one server to two GPUs at the record
rate. A
tile of twelve task agents alone, the support-desk case, would generate
about 2,300 tokens per workflow at a far higher rate and keep several
GPUs busy per server; it is an estimate from the catalog's weights, not
a measured set.
What moves the ratio is what agents do between model calls, which sets
the tokens a server generates per second for a given amount of host
work, and the tokens per GPU, which the model and accelerator set; a
slower serving tier changes residency and the response curve, never the
ratio.

### Sensitivity to the model

The host side of the ratio does not depend on the model; the token side
does, twice: a different model generates a different number of tokens
for the same workflow, and it is served at a different rate per GPU.
The workload's calls were replayed against four model settings and
each answer set kept only the turns whose shape matched the position
(a tool call where a tool call was due, a draft where a draft was due),
so the four token profiles are comparable. The table scales the
record's measured tokens per workflow by each profile's ratio to the
record's own profile, holds the host work per workflow at the
capacity rung (63 core-s, which the model does not change), and takes
each model's serving rate from the same source and with the same
context haircut as the record where one exists.

| Model, as replayed | Tokens per workflow, tile-weighted | Core-ms per token | Generated tokens/s at 0.84 wf/s | Tokens/s per GPU | GPUs one server keeps busy | Cores per GPU |
|---|---|---|---|---|---|---|
| gpt-oss-20b, low reasoning (record) | 7,030 measured | 9.0 | 5,920 | 3,500 | 1.69 | 31 |
| gpt-oss-120b, low reasoning | 4,250 (0.61 of the record) | 14.8 | 3,570 | 1,420 (1,779 at 50 concurrent, same haircut) | 2.5 | 21 |
| Qwen3.8 Flash, thinking off | 3,800 (0.54) | 16.6 | 3,190 | not cited; shown at 3,500 | 0.91 | 58 |
| gpt-oss-20b, medium reasoning | 14,700 (2.1) | 4.3 | 12,350 | 3,500 | 3.5 | 15 |

Two things move the ratio in opposite directions. A larger model
generates fewer tokens for the same work (the 120B and Qwen replays
emit about half the record's tokens, mostly because their tool-call
turns carry little reasoning) and is served more slowly, so the
server keeps more GPUs busy per unit of host work only when the second
effect outweighs the first: for the 120B it does (2.5 GPUs), because its
serving rate is 40% of the 20B's while its tokens are 60%. A higher
reasoning setting on the same model doubles the tokens at the same
serving rate and the ratio doubles with it (3.5 GPUs), which is the
case for stating the reasoning setting with every ratio. Residency
and the response curve move with tokens too, through the model wait,
and are not rescaled here; the host work and the capacity rung are
the same in every row because the host is the limit. Profiles:
`data/capacity/serving/{gptoss20b-low,gptoss120b-low,qwen38flash-nothink,gptoss20b-medium}-faithful`.

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

## 12. Prior versions (superseded)

Everything below was measured with earlier workload definitions and is
superseded by the results of record in section 11. None of it should
be quoted as a capacity, residency, token or ratio figure for this
benchmark; it is kept so the history of the workload can be audited.

- **Enterprise tile without the lookups (v2.1, 7 to 8 September 2026).**
  The same five archetypes with retrieval only in the research agent,
  on allocation 4/1/8/51 (the reranker as one process of four threads,
  the ingest embedder sized for the previous ingestion agent), and with
  the research agent's draft and every record turn answered with
  uncalibrated token counts. Capacity 0.84 workflows/s with 0.96 falling
  behind, 136 resident, 70% of the host busy, 9.6 core-ms per token.
  Sets `set-20260907-184339` and `set-20260908-000736` (seeds 11101,
  11201, 11301; 0.48 to 1.08 workflows/s), evidence commits 57b5ee8 and
  108ae7f, samples `set-11100-mpstat.log` and `set-11100b-mpstat.log`.
- **First v2 dry run (7 September 2026).** One seed on the raw
  gpt-oss-20b profile before shape-faithful filtering, 0.32 to 0.68
  workflows/s, with an engine switch mid-set: `series-10801-20260907-165451`,
  evidence only.
- **Organisation tiles on the previous archetypes (5 to 6 September
  2026).** Research at three retrievals, ingestion parsing 100 pages
  with no OCR, analysts at 40 million rows, code agents at three
  optimised builds, task agents with no lookup, and tokens from a
  weight formula rather than a calibrated model. Enterprise on 4/1/8/51:
  capacity 2.4 workflows/s, 151 resident, 23.1 core-ms per token
  (`set-20260906-163941`, `set-20260906-182610`, photographs
  `photo-10301-20260906-213254`, `photo-10401-20260906-215815`,
  `photo-10501-20260906-222336`, `photo-10301-20260906-202031`,
  `photo-10401-20260906-204350`, `photo-10501-20260906-210709`);
  engineering on 8/2/0/46: 2.4 workflows/s, 150 resident, 19.8
  (`set-20260905-060903`); analytics on 8/2/0/46: 2.6 workflows/s, 137
  resident, 19.8 (`set-20260905-090413`). Their per-core samples were
  `set-10300-mpstat.log`, `set-10300b-mpstat.log`, `set-9900-mpstat.log`
  and `set-10000-mpstat.log`.
- **Light reference tile and earlier certified results (v16 to v17,
  August to early September 2026).** Six-session tiles of light agents
  with retrieval in every workflow (39.8 to 62.7 workflows/s, up to
  2,035 resident), the reranker's cores the limit. Under
  `data/capacity/set-v17-certified` and the archive. These measured a
  different question, retrieval throughput per core, and are not
  comparable to the results of record.

