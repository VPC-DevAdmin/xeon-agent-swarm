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


def test_catalog_has_three_tools():
    cat = toolbox_catalog()
    assert set(cat) == {"web_search", "doc_retrieval", "code_exec"}
    # doc_retrieval grant routes to the server's real tool name, not the nickname.
    assert cat["doc_retrieval"]["tool"] == "search_documents"
    assert cat["code_exec"]["tool"] == "execute_python"


def test_build_toolbox_tools_are_named_and_typed():
    tools = build_toolbox(registry={})  # no URLs configured
    assert set(tools) == {"web_search", "doc_retrieval", "code_exec"}
    assert tools["web_search"].name == "web_search"
    # args schema is exposed so the LLM sees the parameters
    assert "query" in tools["web_search"].args


def test_unconfigured_tool_returns_unavailable_not_crash():
    tools = build_toolbox(registry={})  # web_search has no URL
    out = asyncio.run(tools["web_search"].ainvoke({"query": "anything"}))
    assert "unavailable" in out.lower()


def test_grant_enforcement_per_role():
    """Each profile receives only its granted tools."""
    tools_by_name = build_toolbox(registry={})
    profiles = {p["name"]: p for p in build_subagent_profiles(_FakeMF(), tools_by_name)}

    research_tools = {t.name for t in profiles["research"]["tools"]}
    analysis_tools = {t.name for t in profiles["analysis"]["tools"]}

    assert research_tools == {"web_search", "doc_retrieval"}   # granted
    assert analysis_tools == set()                              # nothing granted
    assert "web_search" not in analysis_tools                   # cannot reach it


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
