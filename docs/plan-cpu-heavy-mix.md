# The CPU-heavy mixes and the CPU:GPU ratio

The thesis under test is that agentic workflows move the ratio of
orchestration processors to GPUs from the 1:8 or 1:4 of chat serving
toward 1:1, because agents do real work on the host between model calls.
The benchmark answers it with a measured constant, **host work per
generated token**, and a formula:

    GPUs per 64-core socket = 64,000 / (core-ms per generated token × generation tokens per second per GPU)

The host side is measured here on the reference server. The GPU side is
a published measurement: 3,500 generation tokens/s per GPU is the
record, the tuned serving rate of the model of record (gpt-oss-20b under
vLLM on one RTX PRO 6000, 4,378 tokens/s at 50 concurrent requests,
Database Mart, August 2026) with a 20% haircut for the context this
workload's calls carry (mean 3,100 prompt tokens; Millstone AI's context
sweep of the same model and card shows about 30% lost from 1K to 8K
context). The band around it is 2,400, the conservative rate at which
the ratio was first stated, and 4,378, the short-context measurement;
NVIDIA's TensorRT-LLM tables and CloudRift put 30B mixture-of-experts
models with 3B active at 8,400 to 9,938 on the same card. The method of
record is `docs/benchmark-methodology.md`, whose section 11 carries the
derivation and the citations; this document is the ratio's own account.

## What moves the ratio and what does not

The ratio moves only through work nobody can call a lever: builds, data
jobs, embedding, and reranking, the things an agent does that a GPU would
not do for it. It does not move with the serving tier's speed: a slower
tier multiplies resident agents and stretches the response curve, and leaves
host work per token where it was. Faster GPUs push the ratio toward 1:1
for a fixed tile; a heavier tile pushes it toward 1:1 for a fixed GPU.

Validation is the tempting lever and stays off the host. A full-context
encoder check costs about 0.6 to 1 core-second on the host against about
5 ms on a GPU; seven per workflow would add more host work than the
research agent's whole cost, and a reader would be right to say the
mechanism is inference the GPU does a hundred times cheaper. Validations
run as calls to the serving tier in every published number. No
generative judging runs on the host in any variant.

## The archetypes and their weights

Five archetypes at production sizes, sizes declared as parameters; host
work is the stand-alone sum of each archetype's steps (the methodology's
cost laws), generated tokens are calibrated: the archetype's model calls
recorded and replayed against gpt-oss-20b at low reasoning effort, and
the recorded sizes answering the same call positions in every measured
run. Every archetype that acts looks things up first, and the lookup is
host work (query embedder, index, reranker on the server) inside the
step, never a model turn.

| Archetype | Declared size | Lookups | Host work per workflow | Generated tokens | Core-ms per token |
|---|---|---|---|---|---|
| Task agent | one ticket: a knowledge-base lookup, one record | 1 at depth 32 | about 1 core-s | 2,300 | 0.5 |
| Research agent | 90 source pages fetched and parsed, nine retrievals | 9 at depth 128 | about 25 core-s | 12,800 | 2 |
| Ingestion agent | 50 PDF pages rendered, OCR'd (Tesseract), redacted, chunked, embedded and indexed | none | about 100 core-s | 3,400 | 30 |
| Data analyst | three jobs over 100 million rows, the second over two periods, reporting the change | 2 at depth 64 | about 190 core-s | 11,200 | 17 |
| Code agent | setup, CI (sanitizer build, both suites, static analysis) and verification over Lua 5.4.7 and SQLite 3.50.4 | 3 at depth 64 | about 150 core-s | 9,500 | 16 |

In a mix, busy cores run at about 0.8 times the summed weights because
sibling threads share physical cores, so a tile's constant can be
estimated from this table before it is measured. The work is the work a
reviewer recognises: a CI run over real source, OCR of real pages, a
weekly report compared to the last one, a brief from fetched pages.
Nothing in it is generated, and nothing in it is a lever: each step is
what that agent does, at a size an enterprise runs it.

## The tiles

Twelve arrivals each; small agents dominate by count, as in a deployment,
and the compute-carrying archetypes set the constant.

| Tile | Task agents | Code agents | Data analysts | Research agents | Ingestion agents | Estimated core-ms per token (0.8 of the summed weights) |
|---|---|---|---|---|---|---|
| Enterprise (a technology-forward company) | 6 | 2 | 2 | 1 | 1 | 10 |
| Engineering (an engineering organisation) | 7 | 3 | 1 | 1 | 0 | 8 |
| Analytics (a data and research organisation) | 6 | 0 | 3 | 2 | 1 | 10 |

## Result (8 September 2026)

The enterprise tile was measured with three seeds and 25-minute holds on
the allocation of record (reranker 8 cores as two processes, query
embedder 2, ingest embedder 3, 51 for the instances and their jobs):

| Tile | Capacity | Resident agents, measured | Generated tokens/s at capacity | Core-ms per token | GPUs the server keeps busy at 3,500 tok/s per GPU (record) | at 2,400 / 4,378 |
|---|---|---|---|---|---|---|
| Enterprise | 0.84 wf/s (0.96 falls behind) | 152 | 5,240 | 9.2 | 1.50 | 2.18 / 1.20 |

The ratio is the server's own: its generated tokens per second against
one GPU's, with no scaling to busy cores. At capacity the server is 76%
busy and keeps 1.5 GPUs of the reference class busy at the record rate;
per core, 9.2 core-ms per token at 3,500 tokens/s is 32 cores per GPU,
two GPUs per fully busy 64-core socket. The constant held at 9.2 to 10.2
across the ladder and the seeds. The residency was confirmed the other way round: 152 sessions held in a closed loop kept 150 agents in flight and completed 0.8 workflows a second at the capacity rung's latencies with no drift over half an hour, in all three seeds, with zero failures. Sets
`data/capacity/set-20260908-025329` and `set-20260908-053752`; full curves in
section 11 of the methodology.

## What this does not claim

The published serving numbers were measured on synthetic prompts of 100
to 1,000 tokens; our calls carry a few thousand tokens of context and
generate about a thousand each, and tokens per GPU on our shapes will
differ until they are measured on them, which is why the record carries
a context haircut and does not credit prefix caching. The path for
that measurement exists: the workload's calls are recorded as a query
set and can be replayed against one GPU of a known class at rising
concurrency (`scripts/replay_query_set.py --sweep --gpus`), and the
stand-in then answers with the recorded timing so the host is measured
under a named model's data. Until then the ratio is a host-side
measurement against a cited serving rate, and the rate's provenance is
printed beside every ratio.
