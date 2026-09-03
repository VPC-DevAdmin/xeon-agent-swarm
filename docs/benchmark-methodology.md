# Agent capacity benchmark methodology

The methodology of record is the living document maintained alongside the
benchmark, currently at workload version 16.1 with offline judge rules
post-2 (capability) and sweep-2 (throughput). It supersedes the archived v8
text (docs/archive/benchmark-methodology-v8.md), which predates several
deliberate revisions and must not be cited:

- **Record-not-refuse for the inference stand-in.** A co-located (loopback)
  stand-in no longer disqualifies a run. Every result instead records the
  stand-in's location, worker count, latency distribution, request-rate
  headroom arithmetic, and its measured share of host CPU on the same
  attribution basis as every other component, so a reader weighs the cost
  rather than trusting an eligibility rule.
- **The serving tier is modeled, not instantaneous.** Every model call
  waits as a remote serving tier would: time to first token plus output
  tokens at a decode rate plus input tokens at a prefill rate, computed
  from the actual payload of that call (CAPACITY_MODEL_TTFT_MS=500,
  DECODE_TPS=100, PREFILL_TPS=8000; +/-20% jitter). A planner call reading
  24k tokens waits several times longer than a validator verdict. The
  three parameters are part of the machine fingerprint, so results under
  different serving assumptions never mix.
- **Evidence ledgers and post-processable judgment.** Every run streams a
  per-unit ledger (submit/completion times, outcome, level, offered rate,
  telemetry samples including host CPU, resident memory in GB and percent,
  and per-group CPU attribution). Verdicts are recomputed from the ledger
  by a versioned offline judge (backend/capacity/judge.py); the in-run
  judge only steers. Rule changes are applied to history by re-judging
  stored ledgers, never by re-running load. Ledgers, judgments, and results
  are versioned in data/capacity/.
- **The deadline anchor and the confidence claim, stated precisely.** A
  certified level's claim is: each workflow type's on-time fraction is at
  least 95%, at 95% confidence jointly across types (Wilson lower bound,
  Bonferroni-split alpha), over the units that level decided. A level fails
  only when the Wilson upper bound refutes the target. The bound covers one
  level; selecting the best of many tested levels and run-to-run variation
  are covered by the repeat series (three runs per certified baseline), not
  by the within-level bound.
- **Throughput by plateau, judged by backlog.** The open-loop driver is a
  dumb generator: it holds a fixed arrival rate for a fixed dwell and never
  reacts to what it sees except for safety stops (resource streak, backlog
  cap). A rate is judged in 30 s windows by the sweep-2 rule: every
  workflow type's on-time fraction against a tier deadline clears the joint
  Wilson bound AND the backlog does not grow (arrivals-to-date minus
  completions-to-date changes by at most max(5, 5% of the window's
  arrivals)). Same-window completion matching was abandoned (sweep-1)
  because, with 30-160 s workflows, a window's completions answer the
  previous window's arrivals. Because workflow latency rivals level dwell,
  rates are run as separate plateaus (one rate per instance start), so
  every cohort completes under the rate that admitted it, and the
  latency-versus-rate curve is read across plateaus.
- **Workload v16.1: the orchestrator earns its context on the box.** Three
  archetypes, tiled equally. The *researcher* (heavy) makes 13 model calls,
  7 validations, and 6 tool calls: each of its three workers runs a real
  retrieval (bench_retrieve) and then a durable record write
  (bench_record). The *comparison* (medium) makes 11 calls, 7 validations,
  4 tool calls: its research worker retrieves once, every worker records.
  The *digest* (light) makes 10 calls, 7 validations, 3 tool calls: records
  only. Contracts are enforced per unit; a unit outside its contract is
  invalid, never silently counted. Retrieval is real work on the host: BM25
  over a 120,000-chunk seeded corpus (SQLite FTS5), reciprocal-rank fusion
  with a modeled off-box dense index (a large vector database stays off
  the box; its 15 ms answer is modeled), a lexical prefilter to 16
  candidates, a cross-encoder rerank of those 16 on the box (INT8
  ms-marco-MiniLM-L-6-v2 on ONNX Runtime), and packing of the winners with
  [chunk-N] citations into the worker's context. Workers cite the chunk
  ids they were given, so grounding is checkable.
- **Retrieval quality is a diagnostic, not a gate.** Capacity is
  invariant to relevance: reranking 16 relevant chunks costs exactly what
  reranking 16 irrelevant ones costs, and the rest of the workflow is
  fixed by contract. The pipeline therefore reports an in-topic fraction
  (share of packed chunks from the query's seeded topic) per retrieval as
  evidence that the pipeline is doing what a production pipeline does, and
  no verdict depends on it.
- **The retrieval tier is sized by physical core and admission-controlled.**
  The box is 64 physical cores with two SMT threads each and an irregular
  sibling map, so allocations are derived from the topology, never written
  as logical ranges (a range such as "56-127" handed the reranker eight
  whole cores plus 48 half-cores whose siblings ran executors). The
  reranker is quantized to INT8 and served through ONNX Runtime with
  dynamic batching (one inference thread per process, up to 128 pairs per
  run), where the int8 GEMMs use the Xeon's AMX/VNNI units; its measured
  cost is about one physical core per three rerank calls per second
  (16 pairs of ~125 tokens) whatever the worker/thread split, and two
  runtime threads on one core's siblings halve each other, so the runtime
  gets one thread per physical core. The reference allocation is 40 cores
  for the reranker, 4 for the embedder, and the remaining 20 whole cores
  for the orchestrator instances, executors, stand-ins, and databases,
  which are pinned there so nothing shares AMX units with the tier. Pinning
  is by cpuset, not quota (quotas on a 128-thread host throttle-thrash).
  Each executor admits at most four reranker calls at a time
  (CAPACITY_RERANK_CONCURRENCY) and backs off exponentially on 429/503, so
  a saturated tier produces queueing that the judge sees as latency, not
  errors. The tier's CPU is attributed to the box totals like every other
  component, following process trees.
- **Retrieval stage timings and the generator's receipt are evidence.**
  Every executor flushes per-stage retrieval percentiles (embed and rerank
  gate wait and call, fuse, pack) every 30 s, and every ledger sample
  carries the arrival generator's own receipt (arrivals shed by its stall
  clamp, ticks fired late). A plateau whose achieved arrival rate is under
  95% of the offered rate is generator-limited and sustains no tier.
- **Rerank depth is a declared dial with a measured law.** The reference
  workload scores 16 candidates per retrieval (40 fused, keyword-prefiltered
  to 16, 12 kept). The tier's ceiling is a pair budget, ~1,300-1,400 scored
  pairs/s on 40 cores at any depth, so the sustainable rate is about
  1,300 / (1.33 calls x depth). Checked at one seed with 5-minute holds
  (series 7601, 7602): depth 32 held 32 workflows/s at the edge (researcher
  p95 85 s, zero failures) and not 40; depth 48 held 20 and not 28; the
  certified 62.7 at depth 16 sits on the same line. CAPACITY_RERANK_DEPTH
  is part of the machine fingerprint, so depths never mix.
- **Context is re-carried, not cached.** Each model call re-sends its full
  context and the serving model charges prefill for all of it. A deployed
  serving tier with prompt caching would charge less for the repeated
  prefix; the benchmark keeps the pessimistic accounting because the
  orchestrator's cost (the thing being measured) does not change with the
  serving tier's cache policy.
- **Accounting defects are counted, never hidden.** The stream adapter
  logs and counts any subagent message it cannot attribute to a delegation
  (metrics.unbound_msgs); a lost tool hop shows up as a contract miss on
  that unit, which the harness records as invalid rather than as success
  or failure.
- **Host power management is part of the fingerprint.** The reference box
  ramps idle cores from 500-2,500 MHz to 3,900 MHz under load with no OS
  frequency governor exposed (firmware-managed), so a call that lands on
  idle cores pays a ramp of tens of milliseconds. At the operating points
  that matter (the knee) the cores are saturated and hot; at light load
  the ramp is inside the reported latency. A BIOS system profile of
  "Performance" would remove it and must be recorded when used.
- **Vocabulary.** A *workflow* is one fixed-size unit of agent work of one
  archetype (contract above). A *session* is a closed-loop driver that
  runs workflows back to back with think time; session count is the
  capability metric. An *agent* in prose means a session. A *subagent* is
  one worker inside a workflow (about three in flight per session).
  *Resident* workflows at a rate are rate x (median latency + think time)
  by Little's law.
- **Throughput, measured (v16.1 certified set, 2026-09-03).** Three plateau
  series (seeds 7201, 7301, 7401) on the four-instance fleet with the tier
  at 40+4 cores, ten-minute holds at 4/8/12/14/16 workflows/s per
  instance, judged by plateau-1 from the ledgers (data/capacity/set-
  20260903-061731/summary.json). Highest plateau every series sustains:
  62.7 workflows/s box-wide (range 62.69-62.86), every tier from
  interactive (45 s) downward, zero failures in 109,383 admitted units at
  that level; researcher p50/p95 34/40 s, comparison 29/33 s, digest
  28/29 s; 2,035 resident sessions (range 2,026-2,038); host 61% of
  logical threads, which understates the box: sampled per hardware thread
  (scripts/core_occupancy.py, seed 7501), 90% of the 64 physical cores
  were occupied - reranker cores 99% (one thread per core by design),
  embedder 80%, the other twenty 72% - and the reranker ran at 91% of its
  measured ceiling (84 of ~92 rerank calls/s). The next offered level, 18/s per
  instance, run under the same three seeds, is not sustained: researcher
  82/189 s, comparison 43/122 s, no tier's bound holds, and the fleet
  delivered 60.8 of 72 offered workflows/s (combined summary in
  data/capacity/set-v16.1-certified/, evidence commit 88e3d34). Open loop is the primary throughput measurement; sizing from
  the measured rate is Derived, no longer Projected.
