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


async def call_tool(tool_name: str, arguments: dict) -> str:
    """Call an MCP tool and return the result as a string for context injection."""
    url = MCP_REGISTRY.get(tool_name)
    if not url:
        return ""

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{url}/mcp", json=payload, timeout=10.0)
            data = resp.json()
            result = data.get("result", {})
            # MCP spec: result.content may be a list of content blocks or a string
            content = result.get("content", "")
            if isinstance(content, list):
                # Extract text from content blocks
                return "\n".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            return str(content)
    except Exception:
        return ""
