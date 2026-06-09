"""
Document retrieval MCP server — thin proxy to the external semantic search endpoint.

This server holds no corpus, no embeddings, no vector index. It exposes an MCP
interface that workers call uniformly, and forwards each call to the sibling
"intelligent data search" project at SEMANTIC_SEARCH_ENDPOINT.

If the endpoint is unreachable, tool calls return a clear error message so the
worker can degrade gracefully (e.g., a vision task uses its fallback_behavior).

Environment variables:
  SEMANTIC_SEARCH_ENDPOINT  Base URL for the external semantic search service
                            (e.g. https://search.internal). Required.
  SEMANTIC_SEARCH_TOKEN     Optional bearer token for the search endpoint.
  SEMANTIC_SEARCH_TIMEOUT   Request timeout in seconds (default 20).

Tools exposed:
  search_documents — text semantic search
  search_images    — image semantic search (returns image refs the worker
                     can pass to the vision model)
  list_corpora     — list available corpus / collection names
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI

logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Doc Retrieval Server (proxy)")

# ── Configuration ─────────────────────────────────────────────────────────────

SEMANTIC_SEARCH_ENDPOINT = os.getenv("SEMANTIC_SEARCH_ENDPOINT", "").rstrip("/")
SEMANTIC_SEARCH_TOKEN = os.getenv("SEMANTIC_SEARCH_TOKEN", "")
SEMANTIC_SEARCH_TIMEOUT = float(os.getenv("SEMANTIC_SEARCH_TIMEOUT", "20"))


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


async def _get(path: str) -> dict[str, Any]:
    base = _require_endpoint()
    url = f"{base}{path}"
    async with httpx.AsyncClient(timeout=SEMANTIC_SEARCH_TIMEOUT) as client:
        resp = await client.get(url, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


# ── Result formatting ─────────────────────────────────────────────────────────

def _format_text_hits(hits: list[dict[str, Any]]) -> str:
    """Format a list of text-search hits as a markdown payload for the worker."""
    if not hits:
        return "No matching documents found."
    lines: list[str] = []
    for i, hit in enumerate(hits, 1):
        title = hit.get("title") or hit.get("doc_title") or "(untitled)"
        corpus = hit.get("corpus") or hit.get("collection") or ""
        source = hit.get("source") or hit.get("url") or ""
        snippet = (hit.get("text") or hit.get("snippet") or "")[:600].rstrip()
        if hit.get("text") and len(hit["text"]) > 600:
            snippet += "…"
        header = f"**[{i}] {title}**"
        if corpus:
            header += f" ({corpus})"
        line = header
        if source:
            line += f"\nSource: {source}"
        line += f"\n{snippet}"
        lines.append(line)
    return "\n\n---\n\n".join(lines)


def _format_image_hits(hits: list[dict[str, Any]]) -> str:
    """Image hits are returned as a JSON string so the caller can parse the URLs.

    Schema: {"hits": [{"url": str, "caption": str, "corpus": str?}, ...]}
    """
    normalized = []
    for hit in hits:
        normalized.append({
            "url":     hit.get("url") or hit.get("source") or "",
            "caption": hit.get("caption") or hit.get("description") or "",
            "corpus":  hit.get("corpus") or hit.get("collection") or "",
        })
    return json.dumps({"hits": normalized})


# ── Tool implementations ──────────────────────────────────────────────────────

async def search_documents(query: str, max_results: int, corpus: str) -> str:
    if not query.strip():
        return "Empty query."
    payload = {"query": query, "max_results": max_results, "corpus": corpus}
    try:
        body = await _post("/v1/search/text", payload)
    except _SearchEndpointUnconfigured as exc:
        return f"Search unavailable: {exc}"
    except httpx.HTTPStatusError as exc:
        logger.error("Text-search HTTP error: %s — body=%s",
                     exc.response.status_code, exc.response.text[:300])
        return f"Search error: HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        logger.error("Text-search transport error: %s", exc)
        return f"Search transport error: {exc!s}"
    return _format_text_hits(body.get("hits") or body.get("results") or [])


async def search_images(query: str, max_results: int, corpus: str) -> str:
    if not query.strip():
        return "Empty query."
    payload = {"query": query, "max_results": max_results, "corpus": corpus}
    try:
        body = await _post("/v1/search/image", payload)
    except _SearchEndpointUnconfigured as exc:
        return f"Image search unavailable: {exc}"
    except httpx.HTTPStatusError as exc:
        # 404 from upstream → endpoint supports text but not images.
        # Surface gracefully so vision workers can fall back.
        if exc.response.status_code == 404:
            return "Image search is not supported by the search service."
        logger.error("Image-search HTTP error: %s — body=%s",
                     exc.response.status_code, exc.response.text[:300])
        return f"Image search error: HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        logger.error("Image-search transport error: %s", exc)
        return f"Image search transport error: {exc!s}"
    return _format_image_hits(body.get("hits") or body.get("results") or [])


async def list_corpora() -> str:
    try:
        body = await _get("/v1/corpora")
    except _SearchEndpointUnconfigured as exc:
        return f"Search unavailable: {exc}"
    except httpx.HTTPStatusError as exc:
        return f"List error: HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"List transport error: {exc!s}"
    corpora = body.get("corpora") or body.get("collections") or []
    if not corpora:
        return "No corpora reported by the search service."
    lines = []
    for c in corpora:
        name = c.get("name") if isinstance(c, dict) else str(c)
        size = c.get("size") or c.get("count") if isinstance(c, dict) else None
        suffix = f" ({size} items)" if size is not None else ""
        lines.append(f"- **{name}**{suffix}")
    return "Available corpora:\n" + "\n".join(lines)


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
