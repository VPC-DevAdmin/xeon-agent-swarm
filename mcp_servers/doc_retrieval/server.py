"""
Document retrieval MCP server — Redis Stack + TEI semantic search.

Replaces the previous ChromaDB stub. Queries are embedded via the TEI
service and searched against the Redis HNSW corpus indices populated by
`backend.corpus.ingester`.

Environment variables:
  REDIS_URL       redis://redis:6379
  TEI_ENDPOINT    http://tei-embedding:8090
  EMBEDDING_DIM   384

Tools exposed:
  search_documents — semantic search across one or all corpora
  list_corpora     — list available corpus names with chunk counts
"""
from __future__ import annotations

import os
import struct

import httpx
import numpy as np
import redis.asyncio as aioredis
from fastapi import FastAPI
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

app = FastAPI(title="MCP Doc Retrieval Server")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
TEI_ENDPOINT = os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090").rstrip("/")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# Known corpus names — must match what the ingester created
_CORPORA = ["ai_hardware", "ai_software", "llm_landscape"]

TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Semantic search over the AI/hardware/LLM corpus. "
            "Returns the most relevant text passages for a query. "
            "Set corpus='all' to search across all domains."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "default": 4,
                    "description": "Number of passages to return (1-10)",
                },
                "corpus": {
                    "type": "string",
                    "default": "all",
                    "description": "Corpus to search: 'ai_hardware', 'ai_software', 'llm_landscape', or 'all'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_corpora",
        "description": "List available corpus names and their chunk counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _embed(query: str) -> list[float]:
    """Embed a single query string via TEI."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{TEI_ENDPOINT}/embed",
            json={"inputs": [query], "truncate": True},
        )
        resp.raise_for_status()
        return resp.json()[0]


def _to_str(val) -> str:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


async def _search_one_corpus(
    r: aioredis.Redis,
    corpus_name: str,
    query_vec: list[float],
    top_k: int,
) -> list[dict]:
    """Run KNN search against one corpus index. Returns hit dicts."""
    index_name = f"idx:{corpus_name}"
    try:
        await r.ft(index_name).info()
    except ResponseError:
        return []   # corpus not seeded yet

    vec_bytes = np.asarray(query_vec, dtype=np.float32).tobytes()
    q = (
        Query(f"*=>[KNN {top_k} @embedding $query_vec AS score]")
        .sort_by("score")
        .return_fields("text", "doc_title", "source", "score")
        .paging(0, top_k)
        .dialect(2)
    )
    res = await r.ft(index_name).search(q, query_params={"query_vec": vec_bytes})
    hits = []
    for doc in res.docs:
        hits.append(
            {
                "corpus": corpus_name,
                "doc_title": _to_str(getattr(doc, "doc_title", "")),
                "text": _to_str(getattr(doc, "text", "")),
                "source": _to_str(getattr(doc, "source", "")),
                "score": float(getattr(doc, "score", 1.0)),
            }
        )
    return hits


# ── Tool implementations ──────────────────────────────────────────────────────

async def search_documents(query: str, max_results: int, corpus: str) -> str:
    if not query.strip():
        return "Empty query."
    try:
        query_vec = await _embed(query)
    except Exception as exc:
        return f"Embedding error: {exc}"

    corpora_to_search = _CORPORA if corpus == "all" else [corpus]

    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    try:
        all_hits: list[dict] = []
        for c in corpora_to_search:
            hits = await _search_one_corpus(r, c, query_vec, top_k=max_results)
            all_hits.extend(hits)
    finally:
        await r.aclose()

    if not all_hits:
        return "No matching documents found in the corpus."

    # Merge-sort by score (ascending cosine distance) and take top max_results
    all_hits.sort(key=lambda h: h["score"])
    top = all_hits[:max_results]

    lines = []
    for i, hit in enumerate(top, 1):
        snippet = hit["text"][:600].rstrip()
        if len(hit["text"]) > 600:
            snippet += "…"
        lines.append(
            f"**[{i}] {hit['doc_title']} ({hit['corpus']})**\n"
            f"Source: {hit['source']}\n"
            f"{snippet}"
        )
    return "\n\n---\n\n".join(lines)


async def list_corpora() -> str:
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    try:
        lines = []
        for c in _CORPORA:
            try:
                info = await r.ft(f"idx:{c}").info()
                if isinstance(info, dict):
                    n = int(_to_str(info.get("num_docs", 0)) or 0)
                else:
                    info_dict = {}
                    for i in range(0, len(info), 2):
                        info_dict[_to_str(info[i])] = info[i + 1] if i + 1 < len(info) else None
                    n = int(_to_str(info_dict.get("num_docs", 0)) or 0)
                lines.append(f"- **{c}**: {n} chunks")
            except ResponseError:
                lines.append(f"- **{c}**: not seeded")
    finally:
        await r.aclose()
    return "Available corpora:\n" + "\n".join(lines)


# ── MCP endpoint ──────────────────────────────────────────────────────────────

@app.post("/mcp")
async def mcp_endpoint(request: dict):
    method = request.get("method")
    req_id = request.get("id", 1)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "search_documents":
            result = await search_documents(
                query=arguments.get("query", ""),
                max_results=min(int(arguments.get("max_results", 4)), 10),
                corpus=arguments.get("corpus", "all"),
            )
        elif tool_name == "list_corpora":
            result = await list_corpora()
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": result}]},
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


@app.get("/health")
def health():
    return {"status": "ok", "server": "mcp-doc-retrieval"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 9002)))
