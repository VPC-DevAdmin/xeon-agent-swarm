"""
Offline unit tests for the managed toolbox and its per-role grant enforcement.

No live MCP servers: build_toolbox produces the catalog unconditionally, and a
tool whose server URL is unset returns a clear unavailable message at call time.
The grant test proves a role gets exactly its worker_roles.yaml-declared tools
and nothing else (Stage 3 acceptance: researcher can call web_search, analysis
can't).
"""
from __future__ import annotations

import asyncio

from backend.agents.toolbox import build_toolbox, toolbox_catalog
from backend.agents.profiles import build_subagent_profiles, grant_map, validation_config


class _FakeMF:
    """ModelFactory stand-in: profiles only store whatever auto() returns."""
    def auto(self, *a, **k):
        return "WORKER_MODEL"


def test_catalog_is_the_curated_set():
    cat = toolbox_catalog()
    # the builtins remain, plus the curated messaging/social/data set
    assert {"web_search", "doc_retrieval", "code_exec"} <= set(cat)
    assert {"telegram", "sms", "sql_database", "csv_file", "x_twitter"} <= set(cat)
    assert cat["telegram"]["category"] == "messaging"
    assert "notify" in cat["telegram"]["capabilities"]


def test_build_toolbox_tools_are_named_and_typed():
    tools = build_toolbox(registry={})  # no URLs configured
    # every catalog entry becomes a live StructuredTool
    assert {"web_search", "telegram", "csv_file"} <= set(tools)
    assert tools["web_search"].name == "web_search"
    # builtin args schema is exposed so the LLM sees the parameters
    assert "query" in tools["web_search"].args


def test_unconfigured_builtin_returns_unavailable_not_crash():
    tools = build_toolbox(registry={})  # web_search has no URL
    out = asyncio.run(tools["web_search"].ainvoke({"query": "anything"}))
    assert "unavailable" in out.lower()


def test_unconfigured_api_tool_reports_not_configured():
    # an api tool with no connector creds degrades cleanly, doesn't crash
    async def _no_creds(_tid):
        return None
    tools = build_toolbox(registry={}, creds_resolver=_no_creds)
    out = asyncio.run(tools["telegram"].ainvoke({"action": "send", "params": {"text": "hi"}}))
    assert "not configured" in out.lower()


def test_every_catalog_tool_is_backed():
    """All catalog tools are builtin or api (no unwired stubs remain), and every
    api tool has a real implementation."""
    from backend.agents import tool_catalog
    from backend.agents.tool_impls import IMPLS
    for tid, s in tool_catalog.catalog().items():
        assert s["backing"] in ("builtin", "api"), f"{tid} is an unwired stub"
        if s["backing"] == "api":
            assert tid in IMPLS, f"{tid} is api but has no impl"


def test_grant_enforcement_per_role():
    """Each profile receives only its granted tools."""
    tools_by_name = build_toolbox(registry={})
    profiles = {p["name"]: p for p in build_subagent_profiles(_FakeMF(), tools_by_name)}

    research_tools = {t.name for t in profiles["research"]["tools"]}
    analysis_tools = {t.name for t in profiles["analysis"]["tools"]}

    assert research_tools == {"web_search", "doc_retrieval"}   # granted
    assert analysis_tools == set()                              # nothing granted
    assert "web_search" not in analysis_tools                   # cannot reach it


def test_tool_user_gets_enabled_tools():
    """The tool_user role is granted exactly the run's enabled tools (dynamic)."""
    tools_by_name = build_toolbox(registry={})
    profiles = {p["name"]: p for p in build_subagent_profiles(
        _FakeMF(), tools_by_name, enabled_tools=["telegram", "csv_file"])}
    tool_user = {t.name for t in profiles["tool_user"]["tools"]}
    assert tool_user == {"telegram", "csv_file"}
    # static roles are unaffected by the enabled selection
    assert {t.name for t in profiles["research"]["tools"]} == {"web_search", "doc_retrieval"}


def test_manifest_lists_enabled_tools():
    from backend.agents import tool_catalog
    m = tool_catalog.manifest(["telegram", "sql_database"])
    assert "telegram" in m and "sql_database" in m
    assert "tool_user" in m                       # tells the planner where to route
    assert tool_catalog.manifest([]) == ""        # no selection → no manifest


def test_grant_map_matches_yaml():
    gm = grant_map()
    assert gm["research"] == ["web_search", "doc_retrieval"]
    assert gm["code"] == ["code_exec"]
    assert gm["analysis"] == []


def test_validation_config_levels():
    vc = validation_config()
    assert vc["research"]["level"] == "judge"
    assert vc["writing"]["level"] == "frontier"
    assert vc["writing"]["tier"] == "tier4"
    assert vc["fact_check"]["level"] == "mechanical"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
