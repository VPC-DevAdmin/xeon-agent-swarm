# The CPU-heavy mixes and the CPU:GPU ratio

The thesis under test is that agentic workflows move the ratio of
orchestration processors to GPUs from the 1:8 or 1:4 of chat serving
toward 1:1, because agents do real work on the host between model calls.
The benchmark answers it with a measured constant, **host work per
generated token**, and a formula:

    GPUs per 64-core socket = 64,000 / (core-ms per generated token × generation tokens per second per GPU)

The host side is measured here on the reference server. The GPU side is a
lab measurement of one RTX PRO 6000 serving a 35B mixture-of-experts
model with 3B active in FP8 (window average 1,300 generation tokens/s
over a draining fleet; peaks 2,400 to 3,800), stated with that
provenance. The method of record is `docs/benchmark-methodology.md`; this
document is the ratio's own account.

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
work is each archetype's stand-alone measurement, generated tokens are
what its model calls make the serving tier produce.

| Archetype | Declared size | Host work per workflow | Generated tokens | Core-ms per token |
|---|---|---|---|---|
| Task agent | one short request, one record | 0.5 core-s | 550 | 0.9 |
| Research agent | three retrievals at rerank depth 128 | 8.5 core-s | 1,800 | 4.7 |
| Ingestion agent | 100 PDF pages parsed, about 480 chunks embedded and indexed | 24 core-s | 550 | 44 |
| Data analyst | three sandboxed jobs over 40 million rows each | 54 core-s | 1,790 | 30 |
| Code agent | three build-and-test steps over Lua 5.4.7 and the SQLite 3.50.4 amalgamation | 92 core-s | 1,800 | 51 |

In a mix, busy cores run at about 0.8 times the summed weights because
sibling threads share physical cores, so a tile's constant can be
estimated from this table before it is measured. The code agent's step is
real, recognizable code: nothing in it is generated, and compiling the
SQLite amalgamation is the best-known compile workload there is.

## The tiles

Twelve arrivals each; small agents dominate by count, as in a deployment,
and the compute-carrying archetypes set the constant.

| Tile | Task agents | Code agents | Data analysts | Research agents | Ingestion agents | Estimated core-ms per token |
|---|---|---|---|---|---|---|
| Enterprise (a technology-forward company) | 6 | 2 | 2 | 1 | 1 | 21 |
| Engineering (an engineering organisation) | 7 | 3 | 1 | 1 | 0 | 21 |
| Analytics (a data and research organisation) | 6 | 0 | 3 | 2 | 1 | 13 |

## Result (5 September 2026)

Each tile was measured with three seeds and ten-minute holds on the
allocation of record (reranker 8 cores, query embedder 2, ingest embedder
8, 46 for the instances and their jobs):

| Tile | Capacity | Resident agents at capacity | Core-ms per token (measured at 2.0/s) | 1,300 tok/s per GPU | 2,400 | 3,800 |
|---|---|---|---|---|---|---|---|
| Enterprise | 2.0 wf/s | 87 | 21.5 | 1 : 2.3 | 1 : 1.2 | 1 : 0.8 |
| Engineering | 2.4 wf/s | 102 | 20.0 | 1 : 2.5 | 1 : 1.3 | 1 : 0.8 |
| Analytics | 2.6 wf/s | 106 | 16.3 | 1 : 3.0 | 1 : 1.6 | 1 : 1.0 |
| Twelve task agents (estimate from the weights) | | | 0.9 | 1 : 55 | 1 : 30 | 1 : 19 |

Every organisation tile crosses 1:1 inside the lab's band. The constant
is flat across each ladder (enterprise 21.5 to 22.7 core-ms per token
from 1.2 to 2.8 workflows/s; engineering 19.0 to 22.4; analytics 16.3 to
19.8), which is what a property of the tile rather than of the load
should do. In every tile the executors' 46 cores are the limit, 83%
occupied at 2.0 workflows/s and 97 to 100% past the cliff; the
reranker never passes 36% and the ingest embedder sits near 50%. Sets
`data/capacity/set-20260905-031403`, `set-20260905-060903`,
`set-20260905-090413`; full curves in section 11 of the methodology.

## What this does not claim

The lab's serving numbers were measured on coding-agent traffic with long
contexts; our calls are shorter and more numerous, and tokens per GPU on
our shapes will differ until they are measured on them. The path for
that measurement exists: the workload's calls are recorded as a query
set and can be replayed against one GPU of a known class at rising
concurrency (`scripts/replay_query_set.py --sweep --gpus`), and the
stand-in then answers with the recorded timing so the host is measured
under a named model's data. Until then the ratio is a host-side
measurement against a stated reference band, and the band's provenance
is printed beside every ratio.
