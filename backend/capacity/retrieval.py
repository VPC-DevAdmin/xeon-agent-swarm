"""
On-box retrieval for the capacity workload (v16): the CPU tier's side of
agentic RAG, real where the box would really do it, modeled where a
deployment would call out.

  on-box, real            sparse search (SQLite FTS5 over a seeded corpus),
                          hybrid fusion (reciprocal rank), lexical rerank
                          (v16a; a small cross-encoder replaces it in v16b),
                          chunk fetch, dedup, token-budget packing
  off-box, modeled        dense ANN search over the large vector index: a
                          deterministic stand-in returns seeded candidates
                          after a modeled wait, exactly as the model tier
                          is modeled (CAPACITY_VDB_MS, default 15)

The corpus is a FIXTURE, like a real document store: built once from
versioned generation parameters (CORPUS_VERSION), shared read-only by every
instance and every run. Per-run determinism comes from the seeded queries,
not from rebuilding the store.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sqlite3
import time
import zlib
from pathlib import Path

CORPUS_VERSION = "v2"
CHUNKS = int(os.getenv("CAPACITY_CORPUS_CHUNKS", "120000"))
TOPICS = 2000
WORDS_PER_CHUNK = 70
# A FIXED location, independent of per-instance results dirs: deriving it
# from CAPACITY_RESULTS_DIR made each fleet instance resolve a different
# path and rebuild its own 100MB copy of the fixture.
CORPUS_DIR = Path(os.getenv("CAPACITY_CORPUS_DIR", "data/capacity/retrieval"))

_WORDS = ("throughput latency quantization bandwidth cache tensor batch "
          "prefill decode scheduler memory socket thread kernel affinity "
          "numa buffer queue token weight gradient checkpoint shard replica "
          "pipeline attention context window layer head embedding vocabulary "
          "sampling temperature logit softmax epoch dataset benchmark "
          "baseline regression profile allocator fragmentation contention "
          "saturation backlog deadline percentile median variance capacity "
          "ceiling").split()


def corpus_path() -> Path:
    return CORPUS_DIR / f"corpus-{CORPUS_VERSION}-{CHUNKS}.db"


def ensure_corpus() -> Path:
    """Build the corpus store once, atomically; reuse forever after.

    Chunks are grouped by topic (chunk ids [t*C/T, (t+1)*C/T) belong to
    topic t) and each body leads with its topic token, so seeded queries
    have deterministic sparse matches and the dense stand-in can return
    in-topic ids that genuinely overlap the sparse list - fusion then has
    something real to fuse."""
    path = corpus_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".building-{os.getpid()}")
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, topic INTEGER, "
                "body TEXT)")
    con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(body, "
                "content=chunks, content_rowid=id)")
    rng = random.Random(f"corpus:{CORPUS_VERSION}")
    per_topic = CHUNKS // TOPICS
    rows = []
    templates = (
        "In the {a} evaluation, the {b} configuration sustained {n} {c} "
        "per second while {d} utilization stayed under {m} percent.",
        "The {a} team reported that {b} {c} degraded once {d} crossed "
        "{n} units, and recommended raising the {a} {c} budget to {m}.",
        "Measurement {n} of the {a} series shows {b} bound by {c}, with "
        "{d} idle; the fix moved the ceiling to {m} {c}.",
        "A {a} regression in {b} was traced to {c} contention at {n} "
        "concurrent {d}, resolved by sharding the {c} pool {m} ways.",
    )
    for cid in range(CHUNKS):
        topic = cid // per_topic
        # Sentence-structured synthetic records: real models get realistic
        # token sequences to embed and rerank; bm25 still gets its terms.
        sents = [f"Record {cid} covers topic{topic} operations."]
        while sum(len(s.split()) for s in sents) < WORDS_PER_CHUNK:
            tpl = templates[rng.randrange(len(templates))]
            sents.append(tpl.format(
                a=_WORDS[rng.randrange(len(_WORDS))],
                b=_WORDS[rng.randrange(len(_WORDS))],
                c=_WORDS[rng.randrange(len(_WORDS))],
                d=_WORDS[rng.randrange(len(_WORDS))],
                n=rng.randrange(2, 5000), m=rng.randrange(10, 96)))
        sents.insert(len(sents) // 2, f"See also the topic{topic} baseline.")
        rows.append((cid, topic, " ".join(sents)))
        if len(rows) >= 5000:
            con.executemany("INSERT INTO chunks VALUES (?,?,?)", rows)
            rows = []
    if rows:
        con.executemany("INSERT INTO chunks VALUES (?,?,?)", rows)
    con.execute("INSERT INTO chunks_fts(rowid, body) "
                "SELECT id, body FROM chunks")
    con.commit()
    con.close()
    try:
        os.replace(tmp, path)      # atomic: concurrent builders race safely
    except OSError:
        tmp.unlink(missing_ok=True)
    return path


_local_con: dict[int, sqlite3.Connection] = {}


def _con() -> sqlite3.Connection:
    """One read-only connection per thread (sqlite objects are not
    thread-portable, and retrieval runs in worker threads)."""
    import threading
    key = threading.get_ident()
    if key not in _local_con:
        _local_con[key] = sqlite3.connect(
            f"file:{ensure_corpus()}?mode=ro", uri=True,
            check_same_thread=False)
    return _local_con[key]


def sparse_search(query: str, k: int = 40) -> list[tuple[int, float]]:
    """BM25 candidates: (chunk_id, score), best first. Real on-box work."""
    terms = re.findall(r"[a-z0-9]+", query.lower())
    match = " OR ".join(terms[:8]) or "capacity"
    rows = _con().execute(
        "SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts "
        "MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (match, k)).fetchall()
    return [(int(r[0]), -float(r[1])) for r in rows]


def dense_search_stub(query: str, k: int = 40) -> list[tuple[int, float]]:
    """The off-box vector service's ANSWER, deterministic from the query:
    in-topic ids with plausible score decay. The WAIT is applied by the
    caller (retrieve), because the wait is the modeled part."""
    m = re.search(r"topic(\d+)", query)
    topic = int(m.group(1)) % TOPICS if m else zlib.crc32(query.encode()) % TOPICS
    per_topic = CHUNKS // TOPICS
    base = topic * per_topic
    rng = random.Random(f"dense:{query}")
    ids = rng.sample(range(base, base + per_topic), min(k, per_topic))
    return [(cid, 1.0 - i * (0.5 / max(1, k))) for i, cid in enumerate(ids)]


def rrf_fuse(dense: list[tuple[int, float]], sparse: list[tuple[int, float]],
             k: int = 60) -> list[int]:
    """Reciprocal-rank fusion of the two candidate lists."""
    score: dict[int, float] = {}
    for lst in (dense, sparse):
        for rank, (cid, _s) in enumerate(lst):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(score.items(), key=lambda x: -x[1])]


def lexical_rerank(query: str, candidates: list[int],
                   top: int = 12) -> list[int]:
    """v16a reranker: term-overlap scoring over the candidates' bodies.

    Deliberately does real per-candidate CPU work (fetch + tokenize +
    score); v16b swaps in a small cross-encoder on the same interface."""
    terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    con = _con()
    for cid in candidates[:32]:
        row = con.execute("SELECT body FROM chunks WHERE id=?",
                          (cid,)).fetchone()
        if not row:
            continue
        body_terms = row[0].split()
        overlap = sum(1 for w in body_terms if w in terms)
        scored.append((overlap / (len(body_terms) or 1), cid))
    scored.sort(reverse=True)
    return [cid for _s, cid in scored[:top]]


def pack(chunk_ids: list[int], budget_words: int = 6000) -> str:
    """Fetch, dedup, and pack winners under the token budget, with chunk-id
    citations a validator can ground against."""
    con = _con()
    out, used = [], 0
    seen: set[int] = set()
    for cid in chunk_ids:
        if cid in seen:
            continue
        seen.add(cid)
        row = con.execute("SELECT body FROM chunks WHERE id=?",
                          (cid,)).fetchone()
        if not row:
            continue
        words = row[0].split()
        if used + len(words) > budget_words:
            break
        used += len(words)
        out.append(f"[chunk-{cid}] " + " ".join(words))
    return "\n\n".join(out)


_http_client = None


def _http():
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def _post_backpressure(url: str, payload: dict):
    """POST to a sized model service, treating 429/503 as backpressure.

    A bounded tier SHEDS when its queue fills; the correct client behavior
    is to wait and retry, so tier saturation shows up as retrieval LATENCY
    in the ledger (the honest, judgeable signal) rather than as 18,500
    failed workflows (observed when 429 was treated as fatal). Backoff is
    capped; a tier that stays saturated past the cap is a real failure."""
    delay = 0.25
    waited = 0.0
    while True:
        r = await _http().post(url, json=payload)
        if r.status_code not in (429, 503):
            r.raise_for_status()
            return r
        if waited >= 120.0:
            r.raise_for_status()
        sleep_for = min(delay, 120.0 - waited) * (0.8 + 0.4 * random.random())
        await asyncio.sleep(sleep_for)
        waited += sleep_for
        delay = min(delay * 2, 10.0)


async def embed_query(query: str) -> list[float] | None:
    """Real on-box query embedding via the TEI service (v16b). Returns None
    when no embedder is configured - the pipeline still runs, the modeled
    dense stand-in being seeded rather than vector-driven either way."""
    url = os.getenv("CAPACITY_EMBED_URL")
    if not url:
        return None
    r = await _post_backpressure(f"{url}/embed", {"inputs": [query]})
    return r.json()[0]


async def cross_rerank(query: str, candidates: list[int],
                       top: int = 12) -> list[int] | None:
    """Real cross-encoder reranking via the TEI service (v16b): one call
    scoring (query, body) for every candidate. Returns None when no
    reranker is configured (the lexical v16a reranker then runs)."""
    url = os.getenv("CAPACITY_RERANK_URL")
    if not url:
        return None
    # Two-stage rerank, standard practice: the cheap lexical scorer
    # prefilters the fused list to 16, and the cross-encoder spends its
    # cycles only on those. Depth 50 cost ~49% of a 128-thread host at 25
    # workflows/s; 16 pairs is one half-batch and a quarter of the demand.
    prefiltered = lexical_rerank(query, candidates, top=16)
    con = _con()
    ids, texts = [], []
    for cid in prefiltered[:16]:
        row = con.execute("SELECT body FROM chunks WHERE id=?",
                          (cid,)).fetchone()
        if row:
            ids.append(cid)
            texts.append(row[0])
    if not texts:
        return []
    # TEI caps client batches at 32; score in slices and merge.
    scored: list[tuple[float, int]] = []
    for start in range(0, len(texts), 32):
        r = await _post_backpressure(
            f"{url}/rerank",
            {"query": query, "texts": texts[start:start + 32],
             "truncate": True})
        for item in r.json():
            scored.append((float(item["score"]), ids[start + item["index"]]))
    scored.sort(reverse=True)
    return [cid for _s, cid in scored[:top]]


async def retrieve(query: str, *, budget_words: int = 6000) -> dict:
    """The full v16a pipeline for one query. Sparse, rerank, fetch, and
    pack run in a worker thread (real CPU, off the event loop); the dense
    call awaits its modeled off-box latency."""
    t0 = time.perf_counter()
    embedded = await embed_query(query)      # real inference when configured
    vdb_ms = float(os.getenv("CAPACITY_VDB_MS", "15") or 0)
    if vdb_ms > 0:
        jitter = 0.8 + 0.4 * ((zlib.crc32(query.encode()) % 1000) / 1000.0)
        await asyncio.sleep(vdb_ms / 1000.0 * jitter)
    dense = dense_search_stub(query)

    def _fuse_side() -> list[int]:
        sparse = sparse_search(query)
        return rrf_fuse(dense, sparse)

    fused = await asyncio.to_thread(_fuse_side)
    winners = await cross_rerank(query, fused)
    reranker = "cross-encoder"
    if winners is None:
        winners = await asyncio.to_thread(lexical_rerank, query, fused)
        reranker = "lexical"
    packed = await asyncio.to_thread(pack, winners, budget_words)
    return {"chunks": winners, "packed": packed,
            "candidates": len(dense), "reranker": reranker,
            "embedded": embedded is not None,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
