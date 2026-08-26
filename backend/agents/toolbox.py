"""
backend/agents/toolbox.py

Turns the curated catalog (config/tool_catalog.yaml, via tool_catalog.py) into live
LangChain tools the workers can call. Three backings:

  - builtin   web_search / doc_retrieval / code_exec — the in-repo MCP servers,
              wrapped over the existing HTTP JSON-RPC transport (protocols/mcp_servers).
  - api       telegram / sms / email / sql_database / csv_file / x_twitter / … —
              real implementations in tool_impls.py, invoked with credentials
              resolved from the connector store at call time.
  - stub      catalogued + configurable but not wired in this build; returns a clear
              message so the planner/worker sees it exists but degrades cleanly.

Credentials for `api` tools come from a Connector named after the tool_id (kind
'tool'): non-secret setup fields in `config`, secret fields Fernet-encrypted. The
resolver is injectable so tests need no DB.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from backend.protocols.mcp_servers import MCP_REGISTRY, call_named_tool
from backend.agents import tool_catalog
from backend.agents.tool_impls import IMPLS


# ── builtin MCP-backed tools: id -> (server nickname, real tool name, arg schema) ──

class _WebSearchArgs(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Maximum results to return")


class _DocRetrievalArgs(BaseModel):
    query: str = Field(description="What to look up in the on-box document corpus")
    max_results: int = Field(default=5, description="Maximum documents to return")


class _CodeExecArgs(BaseModel):
    code: str = Field(description="Python source to execute in the sandbox")


_BUILTIN: dict[str, dict] = {
    "web_search":    {"server": "web_search", "tool": "web_search", "args": _WebSearchArgs},
    "doc_retrieval": {"server": "doc_retrieval", "tool": "search_documents", "args": _DocRetrievalArgs},
    "code_exec":     {"server": "code_exec", "tool": "execute_python", "args": _CodeExecArgs},
}


# ── generic arg schema for api/stub tools ───────────────────────────────────────

class _ToolArgs(BaseModel):
    action: str = Field(default="read",
                        description="What to do: read | send | query | append | update | post | etc.")
    params: dict = Field(default_factory=dict,
                         description="Tool-specific arguments (text, sql, row, to, query, …)")


# ── credential resolution (injectable) ──────────────────────────────────────────

async def _resolve_creds(tool_id: str) -> dict | None:
    """Merge a configured tool's non-secret config + decrypted secrets, or None.

    A configured tool is a Connector named `tool_id`. Opens its own session so it
    can be called from inside a tool coroutine mid-run.
    """
    from backend.db.base import get_sessionmaker
    from backend.repositories import connectors as conn_repo
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            conn = await conn_repo.get_connector_by_name(session, tool_id)
            if conn is None or conn.status != "active":
                return None
            secrets = await conn_repo.resolve_secrets(session, conn.id)
            return {**(conn.config or {}), **secrets}
    except Exception:  # noqa: BLE001 — a resolver failure must degrade, not crash a run
        return None


# ── build ────────────────────────────────────────────────────────────────────

def build_toolbox(tool_ids: list[str] | None = None, *, registry: dict | None = None,
                  creds_resolver=_resolve_creds) -> dict[str, StructuredTool]:
    """Build {tool_id: StructuredTool} for the catalog (or a subset).

    tool_ids:      restrict to these ids; None = the whole catalog. Unknown ids skipped.
    registry:      MCP server URL registry override (tests); defaults to env-driven.
    creds_resolver: async (tool_id) -> creds dict | None; injected for tests.
    """
    reg = registry if registry is not None else MCP_REGISTRY
    ids = tool_ids if tool_ids is not None else tool_catalog.tool_ids()
    tools: dict[str, StructuredTool] = {}
    for tid in ids:
        spec = tool_catalog.spec(tid)
        if spec is None:
            continue
        backing = spec.get("backing", "stub")
        if backing == "builtin" and tid in _BUILTIN:
            tools[tid] = _build_builtin(tid, reg)
        elif backing == "api" and tid in IMPLS:
            tools[tid] = _build_api(tid, spec, creds_resolver)
        else:
            tools[tid] = _build_stub(tid, spec)
    return tools


def _build_builtin(tid: str, reg: dict) -> StructuredTool:
    b = _BUILTIN[tid]
    server, real_tool = b["server"], b["tool"]

    async def _call(server=server, real_tool=real_tool, **kwargs) -> str:
        url = reg.get(server)
        if not url:
            return f"[{real_tool}] unavailable: no URL configured for server '{server}'"
        return await call_named_tool(url, real_tool, kwargs)

    return StructuredTool.from_function(
        coroutine=_call, name=tid,
        description=tool_catalog.spec(tid)["description"], args_schema=b["args"],
    )


def _build_api(tid: str, spec: dict, creds_resolver) -> StructuredTool:
    impl = IMPLS[tid]
    needs_creds = bool(spec.get("setup"))

    async def _call(action: str = "read", params: dict | None = None, _tid=tid,
                    _impl=impl, _needs=needs_creds) -> str:
        creds = await creds_resolver(_tid) if _needs else {}
        if _needs and not creds:
            return (f"[{_tid}] not configured — set it up in the Tools gallery before use.")
        merged = {**(params or {}), "action": action}
        return await _impl(merged, creds or {})

    return StructuredTool.from_function(
        coroutine=_call, name=tid,
        description=spec["description"], args_schema=_ToolArgs,
    )


def _build_stub(tid: str, spec: dict) -> StructuredTool:
    async def _call(action: str = "read", params: dict | None = None, _tid=tid) -> str:
        return (f"[{_tid}] is in the catalog and configurable, but live execution is not "
                f"wired in this build. (Requested action: {action}.)")

    return StructuredTool.from_function(
        coroutine=_call, name=tid,
        description=spec["description"], args_schema=_ToolArgs,
    )


# ── audit/UI catalog view (kept for the existing /toolbox endpoint) ──────────────

def toolbox_catalog() -> dict[str, dict]:
    """{tool_id: {description, category, capabilities, backing}} — catalog summary."""
    return {
        tid: {"description": s["description"], "category": s["category"],
              "capabilities": s.get("capabilities", []), "backing": s.get("backing")}
        for tid, s in tool_catalog.catalog().items()
    }


# ── benchmark tool ────────────────────────────────────────────────────────────
# A REAL record-keeping round-trip for capacity runs: one durable AuditLog
# INSERT plus a deterministic think-time and a seeded-corpus payload back into
# the agent's context. This is what an agent HOST actually does per tool call —
# dispatch, latency, a record written, data injected — with zero external
# dependence, so the work unit stays fixed-size and reproducible.

class _BenchRecordArgs(BaseModel):
    key: str = Field(description="record key to store and retrieve")


def build_bench_tool() -> StructuredTool:
    async def _call(key: str) -> str:
        import asyncio as _asyncio
        import zlib
        from backend.capacity.scenarios import synthetic_text
        from backend.db.base import get_sessionmaker
        from backend.db.models import AuditLog

        # Deterministic 50-150ms "backend latency" per key (crc32, not hash():
        # hash() is salted per process and would break reproducibility).
        delay = 0.05 + (zlib.crc32(key.encode()) % 100) / 1000.0
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                session.add(AuditLog(action="bench.record",
                                     detail={"key": key[:120]}))
                await session.commit()
            stored = "stored"
        except Exception as exc:  # noqa: BLE001 — a failed write IS the signal
            stored = f"store FAILED: {exc}"
        await _asyncio.sleep(delay)
        return (f"[bench_record] {stored} record '{key[:60]}'. Retrieved context: "
                + synthetic_text(f"bench:{key}", 400))

    return StructuredTool.from_function(
        coroutine=_call, name="bench_record",
        description="Store a record durably and retrieve its related context "
                    "(benchmark record-keeping tool).",
        args_schema=_BenchRecordArgs,
    )
