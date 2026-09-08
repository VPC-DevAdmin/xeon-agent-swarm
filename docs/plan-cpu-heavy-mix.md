# The CPU-heavy mixes and the CPU:GPU ratio

The thesis under test is that agentic workflows move the ratio of
orchestration processors to GPUs from the 1:8 or 1:4 of chat serving
toward 1:1, because agents do real work on the host between model calls.
The benchmark answers it with a measured constant, **host work per
generated token**, and a formula:

    GPUs per 64-core socket = 64,000 / (core-ms per generated token × generation tokens per second per GPU)

The host side is measured here on the reference server. The GPU side is
a published measurement: 3,565 output tokens/s per GPU is the record,
the published output-only serving rate of the model of record
(gpt-oss-20b under vLLM on one RTX PRO 6000, 3,753 output tokens/s at 50
concurrent requests, Database Mart, August 2026) less 5%, with prefix
caching assumed for the context this workload's calls carry (mean 3,100
prompt tokens, grown call by call). The band around it is 2,400, the
conservative rate at which the ratio was first stated, and 3,753, the
published rate unadjusted; NVIDIA's TensorRT-LLM tables and CloudRift
put 30B mixture-of-experts models with 3B active at 8,400 to 9,938 on
the same card. The method of
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

| Archetype | Declared size | Lookups | Host work per workflow | Output tokens, judgments included | Core-ms per token |
|---|---|---|---|---|---|
| Task agent | one ticket: a knowledge-base lookup, one record, the worker's answer handed back | 1 at depth 32 | about 1 core-s | 1,490 | 0.7 |
| Research agent | 90 source pages fetched and parsed, nine retrievals | 9 at depth 128 | about 25 core-s | 15,500 | 1.6 |
| Ingestion agent | 50 PDF pages rendered, OCR'd (Tesseract), redacted, chunked, embedded and indexed | none | about 100 core-s | 3,470 | 29 |
| Data analyst | three jobs over 100 million rows, the second over two periods, reporting the change | 2 at depth 64 | about 190 core-s | 12,940 | 15 |
| Code agent | setup, CI (sanitizer build, both suites, static analysis) and verification over Lua 5.4.7 and SQLite 3.50.4 | 3 at depth 64 | about 150 core-s | 12,400 | 12 |

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

## Result (8 September 2026; first seed of the v2.3 set, the other two being measured)

The enterprise tile is measured with three seeds and 25-minute holds on
the allocation of record (reranker 6 cores as two processes, query
embedder 1, ingest embedder 2, 55 for the instances and their jobs):

| Tile | Capacity | Resident agents, measured | Generated tokens/s at capacity | Core-ms per token | GPUs the server keeps busy at 3,565 tok/s per GPU (record) | at 2,400 / 3,753 |
|---|---|---|---|---|---|---|
| Enterprise | 0.84 wf/s (0.90 falls behind) | 143 | 5,660 | 9.6 | 1.59 | 2.36 / 1.51 |

The ratio is the server's own: its generated tokens per second against
one GPU's, with no scaling to busy cores. Generated tokens per second
are the declared mix's output per workflow (judgments included) times
the rate, and busy cores are averaged over the steady window of the
hold. At capacity the server is 85% busy and keeps 1.6 GPUs of the
reference class busy at the record rate; per core, 9.6 core-ms per
token at 3,565 tokens/s is 34 cores per GPU (1.9 GPUs per socket only
as an extrapolation to every core busy, which the reserved tiers do not
permit). The constant held at 9.2 to 9.6 across the ladder. The v2.2
set with the longer task agent measured 152 resident and 9.0 core-ms
per token at the same capacity; the residency photograph on the v2.3
shape follows the set. Set `data/capacity/set-20260908-165351`; full
curves in section 11 of the methodology.

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

## Prior versions (superseded)

Earlier results for this ratio are superseded by the 8 September 2026
result above and should not be quoted: the organisation tiles on the
previous archetype sizes (enterprise 2.4 workflows/s at 23.1 core-ms per
token, engineering and analytics at 19.8, 5 and 6 September), the v2
dry run, and the v2.1 enterprise set without the lookups (0.84
workflows/s at 9.6 core-ms per token, 7 and 8 September). Their sets and
the reasons they were superseded are listed in section 12 of the
methodology.

