"""
backend/agents/toolbox.py

The managed tool catalog. Roles are *granted* subsets of it (worker_roles.yaml),
and the grant is enforced by handing each subagent only its allowed tools
(profiles.py). One catalog, per-role access, auditable usage — the management
story the plan asks for (§5).

Why not langchain-mcp-adapters here: the in-repo mcp_servers/ implement a
simplified JSON-RPC `/mcp` POST (tools/list + tools/call), not full MCP
streamable-HTTP (no initialize handshake, no SSE), so MultiServerMCPClient can't
connect to them as-is and would also require the servers to be live at
agent-build time. Instead each catalog entry is wrapped as a LangChain
StructuredTool over the existing HTTP transport (protocols/mcp_servers). This is
the single swap point: if the servers migrate to FastMCP, replace build_toolbox()
with a MultiServerMCPClient and nothing else changes.

A grant names a *server nickname* (web_search, doc_retrieval, code_exec). A server
may host its tool under a different name (the doc_retrieval server's tool is
`search_documents`), so the catalog records the real tool name to call.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from backend.protocols.mcp_servers import MCP_REGISTRY, call_named_tool


# ── per-tool argument schemas (mirror each server's advertised inputSchema) ──────

class _WebSearchArgs(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Maximum results to return")


class _DocRetrievalArgs(BaseModel):
    query: str = Field(description="What to look up in the on-box document corpus")
    max_results: int = Field(default=5, description="Maximum documents to return")


class _CodeExecArgs(BaseModel):
    code: str = Field(description="Python source to execute in the sandbox")


# The managed catalog: grant nickname -> the server + real tool it routes to.
_CATALOG: dict[str, dict] = {
    "web_search": {
        "server": "web_search", "tool": "web_search",
        "description": "Search the web for current information.",
        "args_schema": _WebSearchArgs,
    },
    "doc_retrieval": {
        "server": "doc_retrieval", "tool": "search_documents",
        "description": "Search the on-box document corpus for relevant passages.",
        "args_schema": _DocRetrievalArgs,
    },
    "code_exec": {
        "server": "code_exec", "tool": "execute_python",
        "description": "Run a short Python snippet in a sandbox and return its output.",
        "args_schema": _CodeExecArgs,
    },
}


def toolbox_catalog() -> dict[str, dict]:
    """{nickname: {description, server, tool}} — the catalog, for the audit/UI view."""
    return {
        nick: {"description": spec["description"], "server": spec["server"],
               "tool": spec["tool"]}
        for nick, spec in _CATALOG.items()
    }


def build_toolbox(registry: dict | None = None) -> dict[str, StructuredTool]:
    """Build {nickname: StructuredTool} for the whole catalog.

    Tools are built unconditionally so the catalog and per-role grants are stable
    regardless of which servers are currently up; a tool whose server URL is unset
    returns a clear unavailable message at call time rather than failing the build.
    registry overridable for tests (defaults to the env-driven MCP_REGISTRY).
    """
    reg = registry if registry is not None else MCP_REGISTRY
    tools: dict[str, StructuredTool] = {}
    for nick, spec in _CATALOG.items():
        server, real_tool = spec["server"], spec["tool"]

        async def _call(server=server, real_tool=real_tool, **kwargs) -> str:
            url = reg.get(server)
            if not url:
                return (f"[toolbox] '{real_tool}' unavailable: no URL configured for "
                        f"server '{server}'")
            return await call_named_tool(url, real_tool, kwargs)

        tools[nick] = StructuredTool.from_function(
            coroutine=_call,
            name=nick,
            description=spec["description"],
            args_schema=spec["args_schema"],
        )
    return tools
