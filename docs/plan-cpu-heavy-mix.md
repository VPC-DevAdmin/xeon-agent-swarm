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

Every archetype keeps the existing contract shape (declared subtasks,
model calls, validations, tool calls) and the same model-call sizes, so the
tokens per workflow stay in the same range and only the host work per
token changes. Costs are planning estimates from the measured cost laws
(35 reranked pairs per core-second, 0.43 core-seconds per million
sandboxed rows, the measured per-archetype floors); each is measured
alone before the tile is certified, as the typical archetypes were.

| Archetype | Tile weight | Host work per workflow | Model calls | Gen tokens | What the work is |
|---|---|---|---|---|---|
| Code agent (build and test) | 2 | ~46 core-s | 13 | ~1,800 | three workers each compile a generated library (twelve units of sixty invertible mixing functions with derived inverses, gcc -O2) and run its 721 property tests in the sandbox; measured 15.1 core-s per build (7.6 compile, 7.4 tests); the verifier is the test result |
| Deep research | 1 | ~13 core-s | 13 | ~1,800 | three retrievals at rerank depth 128 (11 core-s of reranking on the pair law) plus BM25 and fusion |
| Ingestion agent | 1 | ~7 core-s | 4 | ~550 | one worker parses 400 PDF pages in the sandbox (measured 3.9 core-s), then the executor embeds the ~1,900 chunks on the CPU embedder and indexes them; the check query is the verifier |
| Analyst XL | 1 | ~94 core-s | 13 | ~1,800 | three sandboxed jobs over 60M rows each (measured 31.1 core-s per job, eighteen times the reference job) |
| Task ticket | 1 | 0.54 core-s | 4 | 550 | unchanged; keeps the mix honest |
| Ops task | 1 | ~1 core-s (measured 0.4 per job) | 4 | ~550 | modeled on the lab tasks: merge and repair a repository with six conflicting files, then configure, start and probe a service on the sandbox's loopback; eleven checks; mostly waiting rather than computing, as the lab's own tasks were |

Job sizes are declared parameters (`CAPACITY_BUILD_FILES`, `CAPACITY_BUILD_WORK`,
the XL row count, `CAPACITY_INGEST_PAGES`). The first measured sizes (six
build units, 30M rows, 200 pages) landed the tile at about 1:2.7 at the
conservative serving point; the compute-shaped steps were then doubled
before certification so the headline is measured at one declared size.

The ops task is there so the two studies share a row: at 0.4 core-seconds
it is lighter than the lab's tasks (7 to 16 core-seconds, most of it
apt-get and service restarts our sandbox cannot spend), which is itself a
finding: install-configure-verify work does not move the ratio.
Validations are done exactly as in the typical mix, as calls to the
serving tier, so the ratio moves only through work nobody can call a
lever.

Tile estimate at the declared sizes, from the measured per-job costs and
the reference orchestration floors: **about 30 core-s and 1,520 generated
tokens per workflow, 20 core-ms per token**, which is 1.4 GPUs per socket
at 2,400 tok/s and 0.9 at 3,800. The published number is whatever the
certified set measures, with the formula and the band beside it.

| Serving point (generation tok/s per GPU) | Typical mix (certified) | Heavy tile at the declared sizes (~30 core-s/wf) | Lab's own ops tasks |
|---|---|---|---|
| 1,300 (lab window average, draining fleet) | 1 : 61 | 1 : 2.5 | 1 : 8 to 1 : 20 |
| 2,400 (lab peak, conservative) | 1 : 33 | 1 : 1.4 | |
| 3,800 (lab peak, best) | 1 : 21 | 1 : 0.9 | |

Estimated socket-to-GPU ratio, one 64-core socket against one RTX PRO
6000 serving the lab's 35B mixture-of-experts model. The target is the
heavy tile's row: about 1:1.4 at the conservative serving point and 1:1
at the best measured one. Estimates from measured per-job costs and the
reference orchestration floors; the certified set replaces them.

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

At ~30 core-s per workflow one 64-core socket sustains about 1.8
workflows/s (about 110 per minute), driving roughly 2,800 generated
tokens per second and about 18 model calls per second: one RTX PRO 6000
inside its measured peak band. The analyst XL's three 60M-row jobs hold
about 5 GB each, so at that rate the sandboxes hold roughly 120 GB of the
host's 1 TB. The plateau method is unchanged; only the rate ladder
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
   rerank depth; ops-task job (install, configure, verify) with its
   service check; encoder validators served like the reranker for the
   placement sensitivity; scenarios, stand-in policies, tests. Each archetype is
   measured alone (the existing cost-run script) and the tile's core-ms
   per token is checked against the target band before certification.
3. **Certify the heavy mix (1 day box time).** Three seeds, ten-minute
   holds, the cliff above; per-unit stage attribution names the stage
   each archetype's cost lives in; process families and the executor
   spread show the host saturating.
4. **Physical pairing (optional, needs one GPU).** Put the orchestration
   server in front of one RTX PRO 6000 serving the same model in vLLM,
   replace the stand-in with the live tier for the certified point, and
   scrape vLLM's metrics as the lab harness does. This measures tokens
   per second per GPU on our call shapes (2,000-token prompts, 140-token
   outputs) rather than on coding-agent shapes, and shows both sides
   saturating together, which is the 1:1 demonstration in one picture.
5. **Paper (1 day).** A ratio section: the formula, the measured constant
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
