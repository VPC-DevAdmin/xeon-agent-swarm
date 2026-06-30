"""
backend/agents/profiles.py

Ports config/worker_roles.yaml into deepagents declarative SubAgent specs.

A deepagents SubAgent dict (verified against 0.6.10) requires name, description,
system_prompt; optional overrides include tools, model, middleware, interrupt_on.
Workers bind model = mf.auto() so the tier router classifies each subtask.

Tool grants: each role's `tools:` lists MCP server nicknames (web_search,
doc_retrieval, code_exec). At assembly time core.py resolves the live MCP tools
(via langchain-mcp-adapters) into a {tool_name: LangChain tool} map and passes it
here; this module hands each profile only its granted subset. When no tool map is
supplied (headless smoke test), profiles get no tools.

The `general` role maps to deepagents' built-in `general-purpose` fallback name so
the planner has a catch-all when a subtask fits no named role.
"""
from __future__ import annotations

import os

import yaml

_CONFIG_DIR = os.getenv("CONFIG_DIR", "config")
_ROLE_CONFIG = os.path.join(_CONFIG_DIR, "worker_roles.yaml")

# Role key → deepagents subagent name. `general` becomes the built-in fallback.
_NAME_MAP = {"general": "general-purpose"}


def load_roles(path: str | None = None) -> dict[str, dict]:
    """Read worker_roles.yaml → {role_key: role_cfg}."""
    with open(path or _ROLE_CONFIG) as f:
        data = yaml.safe_load(f) or {}
    return data.get("roles", {})


def _granted_tools(role_cfg: dict, tools_by_name: dict | None) -> list:
    """Resolve a role's tool-nickname grant to live LangChain tool objects."""
    if not tools_by_name:
        return []
    granted = []
    for nick in role_cfg.get("tools", []) or []:
        tool = tools_by_name.get(nick)
        if tool is not None:
            granted.append(tool)
    return granted


def build_subagent_profiles(mf, tools_by_name: dict | None = None) -> list[dict]:
    """Build the deepagents `subagents=[...]` list from worker_roles.yaml.

    mf: a ModelFactory (backend.inference.model.ModelFactory). Workers run on
        mf.auto() so the router picks the cheapest sufficient tier per subtask.
    tools_by_name: optional {nickname: LangChain tool} from the MCP adapter wiring.
    """
    worker_model = mf.auto()
    roles = load_roles()
    profiles: list[dict] = []
    for key, cfg in roles.items():
        name = _NAME_MAP.get(key, key)
        profiles.append({
            "name": name,
            "description": cfg.get("description", f"{key} specialist"),
            "system_prompt": cfg.get("system_prompt", "").strip(),
            "tools": _granted_tools(cfg, tools_by_name),
            "model": worker_model,
        })
    return profiles


def grant_map() -> dict[str, list[str]]:
    """{role_name: [granted tool nicknames]} — for the toolbox audit/UI view."""
    roles = load_roles()
    return {
        _NAME_MAP.get(key, key): list(cfg.get("tools", []) or [])
        for key, cfg in roles.items()
    }
