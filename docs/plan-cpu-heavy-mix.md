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

## Result (6 September 2026)

Each tile was measured with three seeds and ten-minute holds; the
enterprise tile on its allocation of record (reranker 4 cores, query
embedder 1, ingest embedder 8, 51 for the instances and their jobs), the
others with the reranker on 8, the query embedder on 2 and 46 application
cores:

| Tile | Capacity | Resident agents, measured | Generated tokens/s at capacity | Core-ms per token | GPUs the server keeps busy at 1,300 / 2,400 / 3,800 tok/s per GPU |
|---|---|---|---|---|---|
| Enterprise | 2.4 wf/s (2.6 falls behind) | 151 | 2,418 | 23.1 | 1.86 / 1.01 / 0.64 |
| Engineering | 2.4 wf/s (2.8 falls behind) | 150 | 2,387 | 19.8 | 1.84 / 0.99 / 0.63 |
| Analytics | 2.6 wf/s (3.2 falls behind) | 137 | 2,658 | 19.8 | 2.04 / 1.11 / 0.70 |

The ratio is the server's own: its generated tokens per second against
one GPU's, with no scaling to busy cores. At capacity each server keeps
one GPU of the reference class busy at the lab's conservative peak. The
enterprise tile's residency was confirmed the other way round: 152
sessions held in a closed loop completed 2.30 workflows a second at the
ladder's latencies with no drift over ten minutes, in all three seeds.
Sets `data/capacity/set-20260906-163941`, `set-20260906-182610`,
`set-20260905-060903`, `set-20260905-090413`; photographs
`photo-10301-20260906-213254`, `photo-10401-20260906-215815`, `photo-10501-20260906-222336`; full curves in section 11 of the
methodology.

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
