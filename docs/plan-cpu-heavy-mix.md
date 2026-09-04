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

| Archetype | Tile weight | Host work per workflow (est.) | Model calls | Gen tokens | What the work is |
|---|---|---|---|---|---|
| Code agent (build and test) | 2 | ~28 core-s | 13 | ~1,800 | three workers each compile a small project and run its test suite in the sandbox (~9 core-s per step); the verifier is the test result |
| Deep research | 1 | ~13.5 core-s | 13 | ~1,800 | three retrievals at rerank depth 128 over a 1M-chunk corpus (11 core-s of reranking on the pair law) plus BM25 and fusion |
| Ingestion agent | 1 | ~12 core-s | 6 | ~900 | parse 200 pages, chunk, embed 1,000 chunks on the CPU embedder, index them; summaries are the model calls |
| Analyst XL | 1 | ~40 core-s | 13 | ~1,800 | three sandboxed jobs over 30M rows each (the existing job at 9x the size) |
| Task ticket | 1 | 0.54 core-s | 4 | 550 | unchanged; keeps the mix honest |
| Ops task (new) | 1 | ~10 core-s (their measured 7 to 16) | 6 | ~2,000 | modeled on the lab tasks: install, configure, start and verify a service in the sandbox (nginx logging, git repair, a cert); the verifier is the service check; most shell time waits rather than computes |

The ops task is there so the two studies share a row: its host cost and
tokens per task should land where the lab measured them (7 to 16
core-seconds per task), which anchors our constant to their data before
the compute-shaped archetypes build on it. Validations are done exactly
as in the typical mix, as calls to the serving tier, so the ratio moves
only through work nobody can call a lever.

Tile estimate: **about 19 core-s and 1,540 generated tokens per
workflow, 12.5 core-ms per token**, which is 2.1 GPUs per socket at
2,400 tok/s and 1.3 at 3,800. One dial closes the rest, and it is a
declared, linear, real-work parameter: **job size**. The analyst's row
count and the code agent's test-suite length are stated in the contract;
doubling the compute-shaped steps takes the tile to about 1.2 GPUs per
socket at the conservative band. The tile is designed to land inside the
1:1 band across the lab's measured range, and the published number is
whatever the certified set measures, with the formula and the band
beside it.

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

At ~19 core-s per workflow one 64-core socket sustains about 2.8
workflows/s (about 170 per minute), driving roughly 4,300 generated
tokens per second and about 30 model calls per second: one RTX PRO 6000
at its measured peak band, two at the window average. The plateau method is unchanged; only the rate ladder
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
