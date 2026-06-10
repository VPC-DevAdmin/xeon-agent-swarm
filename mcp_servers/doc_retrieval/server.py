"""
Document retrieval MCP server — thin proxy to the external semantic search endpoint.

This server holds no corpus, no embeddings, no vector index. It exposes an MCP
interface that workers call uniformly, and forwards each call to the sibling
"intelligent data search" project (vector search + re-rank).

External search contract (the service this proxies to):
    POST {SEMANTIC_SEARCH_ENDPOINT}/query
        body:  {"query": str, "top_k": int}
        reply: {"results": [
                   {"chunk_id", "text", "rerank_score",
                    "dense_score", "metadata", "rank"}, ...]}

The MCP tool interface exposed to workers is stable (search_documents takes
query/max_results/corpus); we remap max_results→top_k internally and surface
each chunk's text with citations derived from its metadata.

If the endpoint is unreachable, tool calls return a clear error message so the
worker can degrade gracefully (e.g., a vision task uses its fallback_behavior).

Environment variables:
  SEMANTIC_SEARCH_ENDPOINT  Base URL of the search service. Required.
                            From inside Docker this is typically
                            http://host.docker.internal:8080 (the service runs
                            on the host's 127.0.0.1:8080).
  SEMANTIC_SEARCH_QUERY_PATH  Path appended for queries (default "/query").
  SEMANTIC_SEARCH_TOKEN     Optional bearer token for the search endpoint.
  SEMANTIC_SEARCH_TIMEOUT   Request timeout in seconds (default 30).

Tools exposed:
  search_documents — text semantic search (vector + re-rank)
  search_images    — not supported by this text search service (clean stub;
                     vision workers fall back via fallback_behavior)
  list_corpora     — the search service is a single flat chunk index
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI

logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Doc Retrieval Server (proxy)")

# ── Configuration ─────────────────────────────────────────────────────────────

SEMANTIC_SEARCH_ENDPOINT = os.getenv("SEMANTIC_SEARCH_ENDPOINT", "").rstrip("/")
SEMANTIC_SEARCH_QUERY_PATH = os.getenv("SEMANTIC_SEARCH_QUERY_PATH", "/query")
SEMANTIC_SEARCH_TOKEN = os.getenv("SEMANTIC_SEARCH_TOKEN", "")
SEMANTIC_SEARCH_TIMEOUT = float(os.getenv("SEMANTIC_SEARCH_TIMEOUT", "30"))


def _auth_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if SEMANTIC_SEARCH_TOKEN:
        headers["Authorization"] = f"Bearer {SEMANTIC_SEARCH_TOKEN}"
    return headers


# ── MCP tool definitions ──────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Semantic search of grounded sources via the external search "
            "service. Returns the most relevant text passages with citations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 4,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of passages to return.",
                },
                "corpus": {
                    "type": "string",
                    "default": "all",
                    "description": (
                        "Optional corpus/collection filter understood by the "
                        "search service. 'all' searches everything."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_images",
        "description": (
            "Semantic search of indexed images (charts, diagrams, photos). "
            "Returns image references the caller can include in a vision-model "
            "prompt. The search service decides what 'image' means."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query describing the desired image content.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 10,
                },
                "corpus": {
                    "type": "string",
                    "default": "all",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_corpora",
        "description": "List corpus / collection names exposed by the search service.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Search-service calls ──────────────────────────────────────────────────────

class _SearchEndpointUnconfigured(RuntimeError):
    pass


def _require_endpoint() -> str:
    if not SEMANTIC_SEARCH_ENDPOINT:
        raise _SearchEndpointUnconfigured(
            "SEMANTIC_SEARCH_ENDPOINT is not set — this MCP server has no "
            "data of its own. Configure it to point at the sibling search "
            "project."
        )
    return SEMANTIC_SEARCH_ENDPOINT


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send a POST to the search service. Raises on transport/HTTP errors."""
    base = _require_endpoint()
    url = f"{base}{path}"
    async with httpx.AsyncClient(timeout=SEMANTIC_SEARCH_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


# ── Result formatting ─────────────────────────────────────────────────────────

def _citation_from_metadata(meta: dict[str, Any]) -> tuple[str, str]:
    """Pull a (title, source) citation from a chunk's metadata, best-effort.

    The search service's metadata schema is open-ended, so we probe the common
    keys. Anything found becomes the citation the worker can cite; missing
    fields just don't render.
    """
    if not isinstance(meta, dict):
        return "(untitled)", ""
    title = (
        meta.get("title") or meta.get("doc_title") or meta.get("document")
        or meta.get("source_title") or meta.get("filename") or "(untitled)"
    )
    source = (
        meta.get("source") or meta.get("url") or meta.get("uri")
        or meta.get("doc_id") or meta.get("path") or ""
    )
    return str(title), str(source)


def _format_text_hits(results: list[dict[str, Any]]) -> str:
    """Format ranked chunks from the search service as markdown for the worker.

    Each result chunk: {chunk_id, text, rerank_score, dense_score, metadata, rank}.
    We render text with a metadata-derived citation and the rerank score so the
    worker can weight and attribute its findings.
    """
    if not results:
        return "No matching documents found."
    # Trust the service's ordering, but sort by rank if present for safety.
    results = sorted(results, key=lambda c: c.get("rank", 0))
    lines: list[str] = []
    for i, chunk in enumerate(results, 1):
        title, source = _citation_from_metadata(chunk.get("metadata") or {})
        text = str(chunk.get("text") or "")
        snippet = text[:700].rstrip() + ("…" if len(text) > 700 else "")
        score = chunk.get("rerank_score")
        header = f"**[{i}] {title}**"
        if isinstance(score, (int, float)):
            header += f"  _(relevance {score:.3f})_"
        block = header
        if source:
            block += f"\nSource: {source}"
        block += f"\n{snippet}"
        lines.append(block)
    return "\n\n---\n\n".join(lines)


# ── Tool implementations ──────────────────────────────────────────────────────

async def search_documents(query: str, max_results: int, corpus: str) -> str:
    if not query.strip():
        return "Empty query."
    # External contract: POST /query {"query", "top_k"} → {"results": [...]}.
    # `corpus` is part of the stable MCP interface but the flat chunk index
    # doesn't filter by it, so it's not forwarded.
    payload = {"query": query, "top_k": max_results}
    try:
        body = await _post(SEMANTIC_SEARCH_QUERY_PATH, payload)
    except _SearchEndpointUnconfigured as exc:
        return f"Search unavailable: {exc}"
    except httpx.HTTPStatusError as exc:
        logger.error("Text-search HTTP error: %s — body=%s",
                     exc.response.status_code, exc.response.text[:300])
        return f"Search error: HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        logger.error("Text-search transport error: %s", exc)
        return f"Search transport error: {exc!s}"
    return _format_text_hits(body.get("results") or [])


async def search_images(query: str, max_results: int, corpus: str) -> str:
    # The current search service indexes text chunks only — no image search.
    # Return a clean signal so vision workers take their fallback_behavior
    # instead of erroring. (When/if the service grows an image endpoint, wire
    # it here.)
    return "Image search is not supported by the configured search service."


async def list_corpora() -> str:
    # The search service is a single flat chunk index (no named corpora).
    if not SEMANTIC_SEARCH_ENDPOINT:
        return ("Search unavailable: SEMANTIC_SEARCH_ENDPOINT is not set.")
    return (
        "The search service is a single flat semantic index "
        "(vector search + re-rank). There are no named corpora to select — "
        "just call search_documents with your query."
    )


# ── MCP JSON-RPC endpoint ─────────────────────────────────────────────────────

@app.post("/mcp")
async def mcp_endpoint(request: dict) -> dict:
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
                max_results=min(int(arguments.get("max_results", 4)), 20),
                corpus=arguments.get("corpus", "all"),
            )
        elif tool_name == "search_images":
            result = await search_images(
                query=arguments.get("query", ""),
                max_results=min(int(arguments.get("max_results", 2)), 10),
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
def health() -> dict:
    return {
        "status": "ok",
        "server": "mcp-doc-retrieval",
        "endpoint_configured": bool(SEMANTIC_SEARCH_ENDPOINT),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 9002)))
