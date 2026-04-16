"""
A/B baseline agent: sends the query to a single large model with a large
context window stuffed with corpus chunks — demonstrating context rot.

Context rot demo:
  1. Retrieve top-20 semantically relevant chunks from all corpora
  2. Pack as many as fit within the model's context budget (~2 800 tokens)
  3. Stream the answer
  4. Count how many source documents the model actually cited
  5. Report: retrieved / included / cited / rot_score

Rot score = 1 - (cited / included).
A score of 0.80 means 80% of the injected context was silently ignored.

Compare with each swarm worker, which receives only 4 highly focused chunks
for its specific subtask — near-100% utilisation at a fraction of the cost.
"""
from __future__ import annotations

import os
import re
import time

import httpx
import numpy as np

import redis.asyncio as aioredis
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

from backend.inference.client import InferenceClient
from backend.schemas.models import (
    SingleModelResult,
    TaskStatus,
    EventType,
    SwarmEvent,
)

# ── Config ────────────────────────────────────────────────────────────────────

_CORPORA = ["ai_hardware", "ai_software", "llm_landscape"]

# How many chunks to retrieve before trimming to fit the context window
_RETRIEVE_TOP_K = 20

# Approximate token budget for injected context.
# The model max_model_len is 4096; reserve ~400 for system + query + answer.
_CONTEXT_TOKEN_BUDGET = 2_800

# Words-to-tokens ratio for English prose (conservative estimate)
_WORDS_PER_TOKEN = 0.75   # i.e. ~1.33 tokens per word

SINGLE_MODEL_SYSTEM = """You are a helpful assistant with access to a knowledge base.
Answer the user's question using the provided context passages where relevant.
Cite sources by mentioning the document title when you use information from them.
Use markdown formatting for readability."""


# ── Corpus retrieval (inlined — single_model runs in the backend container) ──

async def _embed(query: str) -> list[float]:
    tei = os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{tei}/embed", json={"inputs": [query], "truncate": True})
        resp.raise_for_status()
        return resp.json()[0]


def _to_str(val) -> str:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


async def _retrieve_chunks(query: str) -> list[dict]:
    """Retrieve top-K chunks from all corpora, sorted by relevance."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    emb_dim = int(os.getenv("EMBEDDING_DIM", "384"))

    try:
        query_vec = await _embed(query)
    except Exception:
        return []

    vec_bytes = np.asarray(query_vec, dtype=np.float32).tobytes()
    r = aioredis.from_url(redis_url, decode_responses=False)
    all_hits: list[dict] = []

    try:
        for corpus in _CORPORA:
            index = f"idx:{corpus}"
            try:
                await r.ft(index).info()
            except ResponseError:
                continue

            q = (
                Query(f"*=>[KNN {_RETRIEVE_TOP_K} @embedding $query_vec AS score]")
                .sort_by("score")
                .return_fields("text", "doc_title", "source", "score")
                .paging(0, _RETRIEVE_TOP_K)
                .dialect(2)
            )
            res = await r.ft(index).search(q, query_params={"query_vec": vec_bytes})
            for doc in res.docs:
                all_hits.append({
                    "corpus": corpus,
                    "doc_title": _to_str(getattr(doc, "doc_title", "")),
                    "text": _to_str(getattr(doc, "text", "")),
                    "source": _to_str(getattr(doc, "source", "")),
                    "score": float(getattr(doc, "score", 1.0)),
                })
    finally:
        await r.aclose()

    # Merge-sort by cosine distance; deduplicate by (doc_title, first 80 chars)
    all_hits.sort(key=lambda h: h["score"])
    seen: set[str] = set()
    deduped: list[dict] = []
    for h in all_hits:
        key = (h["doc_title"], h["text"][:80])
        if key not in seen:
            seen.add(key)
            deduped.append(h)

    return deduped[:_RETRIEVE_TOP_K]


def _pack_context(chunks: list[dict]) -> tuple[str, int, list[dict]]:
    """
    Pack as many chunks as fit within _CONTEXT_TOKEN_BUDGET.

    Returns (context_string, token_estimate, included_chunks).
    """
    lines: list[str] = ["## Knowledge Base Context\n"]
    token_estimate = 0
    included: list[dict] = []

    for i, chunk in enumerate(chunks, 1):
        snippet = chunk["text"][:800]
        entry = f"### [{i}] {chunk['doc_title']} ({chunk['corpus']})\n{snippet}\n"
        entry_tokens = int(len(entry.split()) / _WORDS_PER_TOKEN)
        if token_estimate + entry_tokens > _CONTEXT_TOKEN_BUDGET:
            break
        lines.append(entry)
        token_estimate += entry_tokens
        included.append(chunk)

    return "\n".join(lines), token_estimate, included


def _count_citations(answer: str, included: list[dict]) -> int:
    """
    Count how many included source titles appear in the model's answer.
    Uses case-insensitive substring matching on the article title.
    """
    cited = 0
    answer_lower = answer.lower()
    for chunk in included:
        title = chunk["doc_title"].lower()
        # Match on the most distinctive part (first two words of title)
        key = " ".join(title.split()[:2]) if title else ""
        if key and key in answer_lower:
            cited += 1
    return cited


# ── Main pipeline ─────────────────────────────────────────────────────────────

def _make_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("SINGLE_MODEL_ENDPOINT", "http://localhost:8083/v1"),
        model=os.getenv("SINGLE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        hardware="cpu",
    )


async def run_single_model(
    run_id: str,
    query: str,
    broadcast,
) -> SingleModelResult:
    """
    Stream the single-model response with corpus context injected.
    Emits single_started / single_token / single_completed events.
    """
    client = _make_client()

    # ── Step 1: retrieve corpus chunks ───────────────────────────────────────
    chunks = await _retrieve_chunks(query)
    retrieved_count = len(chunks)

    context_str, token_estimate, included = _pack_context(chunks)
    included_count = len(included)

    # ── Step 2: build prompt ─────────────────────────────────────────────────
    user_content = f"{context_str}\n\n## Question\n{query}"
    messages = [
        {"role": "system", "content": SINGLE_MODEL_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.single_started,
            run_id=run_id,
            payload={
                "model": client.model,
                "hardware": client.hardware,
                "context_chunks_retrieved": retrieved_count,
                "context_chunks_included": included_count,
                "context_token_estimate": token_estimate,
            },
        ),
    )

    # ── Step 3: stream response ──────────────────────────────────────────────
    t0 = time.perf_counter()
    full_answer = ""

    async for token in client.stream(messages, max_tokens=1024):
        full_answer += token
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.single_token,
                run_id=run_id,
                payload={"token": token},
            ),
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    # ── Step 4: measure context rot ──────────────────────────────────────────
    cited_count = _count_citations(full_answer, included)
    rot_score = round(1.0 - (cited_count / included_count), 3) if included_count > 0 else 0.0

    result = SingleModelResult(
        run_id=run_id,
        query=query,
        answer=full_answer,
        model_used=client.model,
        hardware=client.hardware,
        latency_ms=latency_ms,
        status=TaskStatus.completed,
        context_chunks_retrieved=retrieved_count,
        context_chunks_included=included_count,
        context_chunks_cited=cited_count,
        context_token_estimate=token_estimate,
        context_rot_score=rot_score,
    )

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.single_completed,
            run_id=run_id,
            payload={
                "answer": full_answer,
                "model_used": client.model,
                "hardware": client.hardware,
                "latency_ms": latency_ms,
                "context_chunks_retrieved": retrieved_count,
                "context_chunks_included": included_count,
                "context_chunks_cited": cited_count,
                "context_token_estimate": token_estimate,
                "context_rot_score": rot_score,
            },
        ),
    )

    return result
