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


# deepagents' built-in scaffolding tools (todos + virtual filesystem). Benchmark
# workflows strip these from WORKERS: a small local model can loop on scratchpad
# writes instead of returning its answer, making the work unit variable-size.
# Private-API dependency (_ToolExclusionMiddleware) — pinned to deepagents 0.6.x.
_BUILTIN_TOOL_NAMES = frozenset(
    {"write_todos", "ls", "read_file", "write_file", "edit_file",
     "glob", "grep", "execute"})


def build_subagent_profiles(mf, tools_by_name: dict | None = None,
                            enabled_tools: list[str] | None = None,
                            strip_builtin_tools: bool = False) -> list[dict]:
    """Build the deepagents `subagents=[...]` list from worker_roles.yaml.

    mf: a ModelFactory (backend.inference.model.ModelFactory). Workers run on
        mf.auto() so the router picks the cheapest sufficient tier per subtask.
    tools_by_name: optional {tool_id: LangChain tool} from build_toolbox().
    enabled_tools: the workflow's per-run tool selection. The `tool_user` role is
        granted exactly this set (dynamic), so decomposition can route tool-using
        subtasks to it; the other roles keep their static worker_roles.yaml grants.
    """
    worker_model = mf.auto()
    roles = load_roles()
    profiles: list[dict] = []
    for key, cfg in roles.items():
        name = _NAME_MAP.get(key, key)
        if key == "tool_user":
            grant = {"tools": list(enabled_tools or [])}
        else:
            grant = cfg
        profile = {
            "name": name,
            "description": cfg.get("description", f"{key} specialist"),
            "system_prompt": cfg.get("system_prompt", "").strip(),
            # Benchmark mode (builtins stripped): whatever tools the run built
            # ARE the workload — grant them to every role, bypassing the
            # static worker_roles.yaml grants. Normal runs use role grants.
            "tools": (list(tools_by_name.values())
                      if strip_builtin_tools and tools_by_name
                      else _granted_tools(grant, tools_by_name)),
            "model": worker_model,
        }
        if strip_builtin_tools:
            from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
            profile["middleware"] = [
                _ToolExclusionMiddleware(excluded=_BUILTIN_TOOL_NAMES)]
        profiles.append(profile)
    return profiles


def grant_map() -> dict[str, list[str]]:
    """{role_name: [granted tool nicknames]} — for the toolbox audit/UI view."""
    roles = load_roles()
    return {
        _NAME_MAP.get(key, key): list(cfg.get("tools", []) or [])
        for key, cfg in roles.items()
    }


def validation_config() -> dict[str, dict]:
    """{role_name: {level, tier, rubric, retries}} from worker_roles.yaml.

    Each role's `validation` block (validation_directive.md) declares its validator
    level (mechanical | judge | frontier), the tier the judge runs on, its rubric
    id, and a bounded retry count. Roles without a block inherit env defaults. The
    event adapter reads this to decide whether to run L1/L2 judging on a result.
    """
    default_level = os.getenv("ADL_VALIDATION_DEFAULT_LEVEL", "judge")
    default_tier = os.getenv("ADL_DEFAULT_VALIDATOR_TIER", "tier1")
    default_retries = int(os.getenv("ADL_MAX_VALIDATION_RETRIES", "1"))
    out: dict[str, dict] = {}
    for key, cfg in load_roles().items():
        name = _NAME_MAP.get(key, key)
        v = cfg.get("validation") or {}
        out[name] = {
            "level": v.get("level", default_level),
            "tier": v.get("tier", default_tier),
            "rubric": v.get("rubric"),
            "retries": int(v.get("retries", default_retries)),
        }
    return out
