"""
MCP server registry and tool routing.

Workers call call_tool(tool_name, arguments) to invoke an MCP server.
The client speaks JSON-RPC 2.0 over HTTP (streamable-HTTP transport from MCP spec).
"""
import os
import httpx

MCP_REGISTRY: dict[str, str | None] = {
    "web_search":    os.getenv("MCP_WEB_SEARCH_URL"),
    "doc_retrieval": os.getenv("MCP_DOC_RETRIEVAL_URL"),
    "code_exec":     os.getenv("MCP_CODE_EXEC_URL"),
}


async def list_tools(server_url: str) -> list[dict]:
    """Fetch the list of tools advertised by an MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 1,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{server_url}/mcp", json=payload, timeout=5.0)
        data = resp.json()
        return data.get("result", {}).get("tools", [])


def _extract_content(data: dict) -> str:
    """Pull the text payload out of an MCP tools/call JSON-RPC response."""
    result = data.get("result", {})
    # MCP spec: result.content may be a list of content blocks or a string
    content = result.get("content", "")
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return str(content)


async def call_named_tool(server_url: str, tool_name: str, arguments: dict) -> str:
    """Call a specific tool on a known server URL.

    The toolbox layer (backend/agents/toolbox.py) uses this: a role's grant names a
    server nickname, but the server may host the tool under a different name (e.g.
    the doc_retrieval server hosts `search_documents`). This posts the REAL tool
    name to the given URL, which the per-nickname registry lookup in call_tool can't
    express. Returns the text content, or "" on any transport error.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server_url}/mcp", json=payload, timeout=10.0)
            return _extract_content(resp.json())
    except Exception:
        return ""


async def call_tool(tool_name: str, arguments: dict) -> str:
    """Call an MCP tool by nickname (nickname == advertised tool name) and return
    the result as a string for context injection."""
    url = MCP_REGISTRY.get(tool_name)
    if not url:
        return ""
    return await call_named_tool(url, tool_name, arguments)
