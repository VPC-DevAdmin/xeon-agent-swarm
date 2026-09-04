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
| Encoder validator on every validation | all | ~1 core-s per validation | 0 | 0 | the seven validations per workflow become full-context encoder checks (cross-encoder or NLI class, INT8 on AMX, one forward pass, no generation) instead of generative model calls |

The ops task is there so the two studies share a row: its host cost and
tokens per task should land where the lab measured them, which anchors
our constant to their data before the compute-shaped archetypes build on
it. Tile estimate with the encoder validators: **about 25 core-s and
1,000 generated tokens per workflow, 25 core-ms per token**, which is
1.1 GPUs per socket at 2,400 tok/s and 0.7 at 3,800. The validators do
two things at once: they add real host work, and they remove the seven
short generative calls per workflow from the GPU, which is the larger
effect on the ratio. Two further dials are legitimate enterprise choices
rather than benchmark tricks:

- **Job size.** The analyst's row count and the code agent's test-suite
  length are declared parameters; the cost is linear in both.
- **A generative judge for the final grade.** One per workflow, on the
  GPU tier by default; a labeled variant runs it as a small language
  model on the host (INT8 on AMX, about 15 core-s and 3 to 5 s per
  grade). See the next section for why it is a variant and not the
  baseline.

## Validators on the host, the judge on the GPU

The worry about a CPU-side judge is right: token generation is what GPUs
are for, and a saturated host should not spend 15 core-seconds producing
100 tokens that a GPU produces in a twentieth of a GPU-second. The plan
therefore splits validation into the two things it actually is.

**Checks that do not generate go on the host.** Most validations in an
agent workflow are judgments over a context, not text: is this section
grounded in the retrieved passages, does this output satisfy the schema,
does the test report say pass, is this content within policy. An encoder
model answers those in one forward pass with no decode loop, and the
host already serves that class of model on AMX: the reranker's 35 pairs
per core-second at 125 tokens scales to roughly one full-context check
per core-second. These checks are cheap where they run, need no GPU
scheduling, keep the retrieved context on the host instead of shipping it
to the serving tier a second time, and are bit-reproducible with fixed
threads, which matters for a benchmark verifier. Every one of them moved
off the GPU is a short generative call the serving tier no longer has to
schedule, and short calls are the least efficient work a batched decoder
does. This is the mainstream enterprise pattern for guardrails and
gating, so it is defensible as realism, not as a way to make the host
look busy.

**Generation stays on the GPU by default.** A grade that has to be written
out (a rubric score with reasons, a synthesis critique) is generation, and
the GPU is the right place for it whenever the GPU tier has capacity. The
CPU variant is kept for two reasons a reader may weigh differently:
independence, since a judge on a different model family and different
hardware is a stronger check than a tier grading its own output; and
availability, since at the lab's density boundary the GPU is the binding
resource with zero headroom while the host had a third of its cores idle
at our certified point, so a host-side judge converts idle orchestration
capacity into serving capacity. Both variants are measured and both are
published with their cost per workflow and their latency, and the
headline ratio uses the GPU-judge baseline.

The tile is designed to land inside the 1:1 band across the lab's
measured range, and the published number is whatever the certified set
measures, with the formula and the band beside it.

## What the box does at the heavy mix

At ~25 core-s per workflow one 64-core socket sustains about 2.2
workflows/s (about 130 per minute), driving roughly 2,200 generated
tokens per second and about 10 generative calls per second: one RTX PRO
6000 near its measured peak band. The plateau method is unchanged; only the rate ladder
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
   service check; encoder validators served like the reranker; the
   optional host-side judge; scenarios, stand-in policies, tests. Each archetype is
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

## What this does not claim

The lab's serving numbers were measured on coding-agent traffic with long
contexts; our calls are shorter and more numerous, and tokens per GPU on
our shapes will differ until phase 4 measures them. Slower serving does
not move the ratio (it multiplies resident sessions instead); faster
serving moves it toward 1:1, because the host work per token is fixed by
the workflow while the GPU's tokens per second are not.
