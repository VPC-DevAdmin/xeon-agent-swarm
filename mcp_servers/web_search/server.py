"""
Web search MCP server.

Uses Brave Search API when BRAVE_API_KEY is set, falls back to DuckDuckGo.
Speaks JSON-RPC 2.0 over HTTP (MCP streamable-HTTP transport).
"""
import os
import httpx
from fastapi import FastAPI

app = FastAPI(title="MCP Web Search Server")

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    }
]


async def brave_search(query: str, max_results: int) -> str:
    api_key = os.getenv("BRAVE_API_KEY", "")
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": max_results}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=10.0,
        )
        data = resp.json()
    results = data.get("web", {}).get("results", [])
    lines = []
    for r in results[:max_results]:
        lines.append(f"**{r.get('title', '')}**\n{r.get('url', '')}\n{r.get('description', '')}")
    return "\n\n".join(lines)


async def duckduckgo_search(query: str, max_results: int) -> str:
    """Lightweight DuckDuckGo instant answer API — no key required."""
    params = {"q": query, "format": "json", "no_html": "1", "no_redirect": "1"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params=params,
            timeout=10.0,
            follow_redirects=True,
        )
        data = resp.json()

    lines = []
    if data.get("AbstractText"):
        lines.append(f"**Summary**\n{data['AbstractText']}\n{data.get('AbstractURL', '')}")

    for topic in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            lines.append(f"- {topic['Text']}")

    return "\n\n".join(lines) if lines else f"No results found for: {query}"


async def do_web_search(query: str, max_results: int) -> str:
    if os.getenv("BRAVE_API_KEY"):
        try:
            return await brave_search(query, max_results)
        except Exception:
            pass
    return await duckduckgo_search(query, max_results)


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

        if tool_name == "web_search":
            result = await do_web_search(
                arguments.get("query", ""),
                arguments.get("max_results", 5),
            )
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
    return {"status": "ok", "server": "mcp-web-search"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 9001)))
