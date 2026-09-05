# Plan: a CPU-heavy mix that lands the orchestration-to-GPU ratio near 1:1

## Why a second mix

The certified typical mix fixes one constant: the host spends **0.81
core-milliseconds of orchestration per generated token** (43 core-seconds
per second of host work behind 53,000 generated tokens per second at 40
workflows/s). The ratio of CPU sockets to GPUs then follows from how many
tokens one GPU delivers:

    GPUs per 64-core socket = 64,000 / (core-ms per token x generation tok/s per GPU)

Against the serving rates measured on the Dell lab R770 (one RTX PRO 6000
per vLLM replica, a 35B mixture-of-experts model with 3B active, FP8), the
typical mix needs 21 to 33 GPUs per socket at the lab's peak band (2,400
to 3,800 tok/s per GPU) and about 60 at its window average (1,300 tok/s,
measured over a draining fleet, so a floor rather than a rate). Those lab
figures are real and not tuned, which is why the plan uses them as the
reference band instead of vendor throughput claims.

To reach 1:1 on the same GPU, the workload has to carry **17 to 27
core-ms of host work per generated token**, 20 to 33 times the typical
mix. That is not a tuning change; it is a different kind of agent work,
and it has to be real work with a verifiable output, or the result is a
CPU burner and the reader will say so.

The benchmark therefore runs two mixes under one method: the **typical
mix** already certified, and a **CPU-heavy mix** built from the archetypes
below. Both report the same constant, the same ratio formula, and the
same evidence.

## The CPU-heavy tile

*Superseded by the three organisation tiles of five archetypes; see the
Result at the end. Kept as the plan that was executed.*

Every archetype keeps the existing contract shape (declared subtasks,
model calls, validations, tool calls) and the same model-call sizes, so the
tokens per workflow stay in the same range and only the host work per
token changes. Costs are planning estimates from the measured cost laws
(35 reranked pairs per core-second, 0.43 core-seconds per million
sandboxed rows, the measured per-archetype floors); each is measured
alone before the tile is certified, as the typical archetypes were.

| Archetype | Tile weight | Host work per workflow | Model calls | Gen tokens | What the work is |
|---|---|---|---|---|---|
| Code agent (build and test) | 2 | ~118 core-s | 13 | ~1,800 | three workers each build a real working tree from vendored source in the sandbox, Lua 5.4.7 through its own Makefile and the SQLite 3.50.4 amalgamation, both with gcc -O2, and run both suites (Lua's own tests; an integration script against the built engine: schema, 300k rows, index, aggregates, integrity check); measured 39 core-s per build-and-test step (38 building 327,000 lines across 63 files, 1.1 for the suites); the verifier is the suites' result |
| Deep research | 1 | ~13 core-s | 13 | ~1,800 | three retrievals at rerank depth 128 (11 core-s of reranking on the pair law) plus BM25 and fusion |
| Ingestion agent | 1 | ~25 core-s | 4 | ~550 | one worker parses 100 PDF pages in the sandbox (about 1 core-s), then the executor embeds the ~480 chunks on a separate ingest embedder (measured 22 chunks per second per core in FP32, so about 22 core-s of embedding) and indexes them; the check query is the verifier. Ingestion is embedding-bound: the parse is trivial next to it, which is why the ingest embedder is its own tier |
| Analyst XL | 1 | ~94 core-s | 13 | ~1,800 | three sandboxed jobs over 60M rows each (measured 31.1 core-s per job, eighteen times the reference job) |
| Task ticket | 1 | 0.54 core-s | 4 | 550 | unchanged; the short-lived agent every deployment has |

Job sizes are declared parameters (the vendored project, the XL row
count, `CAPACITY_INGEST_PAGES`, the rerank depth) and are stated with the
result. The build step is real, recognizable code so it can be reported as such:
nothing in it is generated, and compiling the SQLite amalgamation is the
best-known compile workload there is.

An ops-style archetype modeled on the lab's install-configure-verify
tasks was built and dropped: at 0.4 core-seconds per task it only waited
(git and a service round trip), and an agent that waits does not belong
in a mix whose purpose is host work. The lab's own ops tasks, at 7 to 16
core-seconds and 2 to 5 core-ms per token, remain the comparison row.
Validations are done exactly as in the typical mix, as calls to the
serving tier, so the ratio moves only through work nobody can call a
lever.

Tile estimate at the declared sizes, from the measured per-job costs and
the reference orchestration floors: **about 59 core-s and 1,380 generated
tokens per workflow, 43 core-ms per token**, which is 0.6 GPUs per socket
at 2,400 tok/s, 0.4 at 3,800, and 1.2 at the lab's window average. The
first check plateau of the tile at 1.2 workflows/s (series 9490, before
the ingest tier was sized) held with the four other archetypes at their
stand-alone latencies and the host at 58% of threads. The published number is whatever
the certified set measures, with the formula and the band beside it.

| Serving point (generation tok/s per GPU) | Typical mix (certified) | Heavy tile at the declared sizes (~58 core-s/wf) | Lab's own ops tasks |
|---|---|---|---|
| 1,300 (lab window average, draining fleet) | 1 : 61 | 1 : 1.2 | 1 : 8 to 1 : 20 |
| 2,400 (lab peak, conservative) | 1 : 33 | 1 : 0.6 | |
| 3,800 (lab peak, best) | 1 : 21 | 1 : 0.4 | |

Estimated socket-to-GPU ratio, one 64-core socket against one RTX PRO
6000 serving the lab's 35B mixture-of-experts model. The heavy tile's
row crosses 1:1 inside the lab's measured band: about 1:1.2 at the
window average and 1:0.6 at the conservative peak, meaning one
orchestration socket then needs less than one GPU of that class.
Estimates from measured per-job costs and the reference orchestration
floors; the certified set replaces them.

## Where small-model inference runs is a sensitivity, not the result

Validation is the tempting lever, and the plan deliberately keeps it out
of the baseline. Most validations in these workflows are judgments over a
context rather than text (grounding, schema, test result, policy), and an
encoder model answers each in one forward pass on the host, the same
class of work the host already does for reranking. But a full-context
check costs about 0.6 to 1 core-second on the host against about 5 ms on
a GPU; seven per workflow would be seven times the host's entire current
cost per workflow and would cut the typical mix from 40 to about 8
workflows/s. Placing them on the host would move the ratio by that
factor, and a reader would be right to say the mechanism is inference
the GPU does a hundred times cheaper.

The plan therefore treats placement as a **published sensitivity applied
to both mixes**: each mix is reported with validations on the serving
tier (the baseline and the headline) and with encoder validators on the
host, each with the per-check cost stated for both placements. The
reader sees that where small-model inference runs is a design choice
with a known effect on the ratio, and the paper's claim rests on the
baseline. A generative judge on the host is not run in any variant: its
only effect is the one a critic would name, and the host produces
tokens at a rate no serving tier would accept.

## What the box does at the heavy mix

At ~58 core-s per workflow one 64-core socket sustains about 0.9
workflows/s (about 55 per minute), driving roughly 1,300 generated tokens
per second and about 9 model calls per second: one RTX PRO 6000 at the
lab's window average. The analyst XL's three 60M-row jobs hold about
5 GB each, so at that rate the sandboxes hold roughly 50 GB of the host's
1 TB; a code agent's step holds a working copy of both projects, about
15 MB. The plateau method is unchanged; only the rate ladder
moves (0.25 to 1 per instance instead of 4 to 12). The allocation is
re-derived from the cost laws before the set, since the heavy mix shifts
work from the reranker tier to the sandbox side.

## Phases

1. **Synchronized typical-mix run (box time only, 1 day).** Re-run the
   certified typical set with the stand-in at the lab's boundary serving
   rates (about 0.5 s to first token, 2,000 prompt tokens per second per
   request, 20 output tokens per second per request). Capacity in
   workflows/s is unchanged; resident sessions rise from ~1,270 to
   ~3,500 and the responsive tier becomes the certified one. This puts
   our latency and residency on the same serving curve as the lab data.
2. **Build the heavy archetypes (3 to 4 days).** Build-and-test sandbox
   job with a real project and test suite; XL data job size; ingestion
   tool (parse, chunk, embed via the CPU embedder, index); per-archetype
   rerank depth; encoder validators served like the reranker for the
   placement sensitivity; scenarios, stand-in policies, tests. Each archetype is
   measured alone (the existing cost-run script) and the tile's core-ms
   per token is checked against the target band before certification.
3. **Certify the heavy mix (1 day box time).** Three seeds, ten-minute
   holds, the cliff above; per-unit stage attribution names the stage
   each archetype's cost lives in; process families and the executor
   spread show the host saturating.
4. **Recorded serving profile against one known GPU (an API key and a
   dedicated endpoint or rented instance; tens of dollars).** Capture the
   heavy mix's query set from a traced run and replay it with
   `scripts/replay_query_set.py --sweep --gpus 1` against a single-GPU
   endpoint serving an open-weight model (a Together dedicated endpoint
   on one H100 or RTX PRO 6000, or one rented instance running vLLM):
   concurrency doubles until aggregate generation throughput stops
   rising, and that ceiling is the GPU's tokens per second on this
   workload's own calls. `scripts/ratio_from_profile.py` then computes
   GPUs per socket from two measurements and nothing typed in: host
   core-ms per generated token from a plateau's ledgers, and tokens per
   GPU from the recording. The same recording gives the per-call timing
   the stand-in replays in the certified set, the real token counts, and
   a throughput-versus-concurrency curve of our own. A shared serverless
   endpoint gives the timing and tokens but not the GPU count, so it
   serves the "host under a production model" run, not the ratio.
   Re-recording for another model or GPU is the same 45 minutes.
5. **Physical pairing (optional, needs the GPU in the same rack).** The
   same replay and sweep against a GPU the orchestration server can reach
   directly, with the stand-in replaced by the live tier for the
   certified point, showing both sides saturating together.
6. **Paper (1 day).** A ratio section: the formula, the measured constant
   for each mix, a chart of GPUs per socket against GPU throughput with
   the lab band marked, and the two archetype cost badges sets.

## Acceptance

- Hardware is the limit on both sides at the certified point: host cores
  full, and either the GPU's measured ceiling or the stand-in's
  boundary rates.
- Every job has a verifiable output (tests pass, document parsed and
  indexed, aggregates checked), zero failures through the certified rate,
  three-series spread under 1%.
- Every cost is published per archetype and per stage from the ledger
  rows, so any customer mix can be placed on the same curve.
- The ratio is reported as a function of GPU throughput with the
  measured band, never as a single number without its serving point.
- The headline ratio for each mix uses validations on the serving tier;
  host-side validators appear only in the placement sensitivity, with
  their per-check cost on both sides. No generative judging runs on the
  host in any published variant.

## What this does not claim

The lab's serving numbers were measured on coding-agent traffic with long
contexts; our calls are shorter and more numerous, and tokens per GPU on
our shapes will differ until phase 4 measures them. Slower serving does
not move the ratio (it multiplies resident sessions instead); faster
serving moves it toward 1:1, because the host work per token is fixed by
the workflow while the GPU's tokens per second are not.

## Result (5 September 2026)

The six-session heavy tile above was measured first (set
`data/capacity/set-20260904-213229`: capacity 1.2 workflows/s, 31
core-ms per token, 1:0.9 at 2,400 tokens/s). It was then superseded when
the catalog settled on five archetypes with no size variants (research
agent at depth 128, data analyst at 40 million rows, code agent,
ingestion agent, task agent) and three organisation-shaped tiles of
twelve sessions, all certified with three seeds and ten-minute holds:

| Tile | Composition (of twelve) | Capacity | Responsive certified | Core-ms per token | 1,300 tok/s per GPU | 2,400 | 3,800 |
|---|---|---|---|---|---|---|---|
| Enterprise | 2 code, 2 analyst, 1 research, 1 ingestion, 6 task | 2.0 wf/s | 2.0 (87 resident) | 21.5 | 1 : 2.3 | 1 : 1.2 | 1 : 0.8 |
| Engineering | 3 code, 1 analyst, 1 research, 7 task | 2.4 wf/s | 2.0 (88 resident); attended 2.4 (102) | 20.0 | 1 : 2.5 | 1 : 1.3 | 1 : 0.8 |
| Analytics | 3 analyst, 2 research, 1 ingestion, 6 task | 2.6 wf/s | 2.0 (72 resident); attended 2.6 (106) | 16.3 | 1 : 3.0 | 1 : 1.6 | 1 : 1.0 |
| Reference tile (light agents) | reference archetypes | 40 wf/s | 39.8 | 0.74 | 1 : 66 | 1 : 36 | 1 : 23 |

Every organisation mix crosses 1:1 inside the lab's band; the constant
is flat across each ladder (enterprise 21.5 to 22.7 core-ms per token
from 1.2 to 2.8 workflows/s). Sets `set-20260905-031403`,
`set-20260905-060903`, `set-20260905-090413`. Full detail is section 11
of `docs/benchmark-methodology.md`. Resident agents are rate times mean
time in system (Little's law); the judge used the median until 5 September,
which in a tile that is half task agents understated residency about four
times.
