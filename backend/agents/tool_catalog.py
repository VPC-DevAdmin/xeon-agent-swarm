"""
backend/agents/tool_catalog.py

Loads the curated tool catalog (config/tool_catalog.yaml) and derives the views
the rest of the system needs:

  - catalog()            the full catalog for the /tools API and the gallery UI
  - manifest(enabled)    the compact tool list injected into the planner prompt so
                         decomposition can compose tasks around available tools
  - setup_fields(id)     the fields a user provides to configure a tool
  - is_write(id)         whether a tool acts on the outside world (rides the gate)

One catalog, three consumers (API, planner, executor) — no hard-coded tool lists
scattered across the codebase.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

_CONFIG_DIR = os.getenv("CONFIG_DIR", "config")
_CATALOG_PATH = os.path.join(_CONFIG_DIR, "tool_catalog.yaml")

CATEGORY_ORDER = ["messaging", "social", "data", "knowledge", "dev"]


@lru_cache(maxsize=1)
def _load() -> dict[str, dict]:
    with open(_CATALOG_PATH) as f:
        data = yaml.safe_load(f) or {}
    tools = data.get("tools", {})
    # normalize: guarantee the keys every consumer reads
    for tid, spec in tools.items():
        spec.setdefault("name", tid)
        spec.setdefault("category", "dev")
        spec.setdefault("description", "")
        spec.setdefault("capabilities", [])
        spec.setdefault("backing", "stub")
        spec.setdefault("write_risk", False)
        spec.setdefault("setup", [])
    return tools


def catalog() -> dict[str, dict]:
    """{tool_id: spec} for the whole catalog (spec includes name, category,
    description, capabilities, backing, write_risk, setup)."""
    return dict(_load())


def catalog_list() -> list[dict]:
    """Catalog as an ordered list (by category, then name) with `id` folded in —
    the shape the /tools API returns."""
    items = [{"id": tid, **spec} for tid, spec in _load().items()]
    items.sort(key=lambda t: (_cat_index(t["category"]), t["name"].lower()))
    return items


def _cat_index(cat: str) -> int:
    return CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else len(CATEGORY_ORDER)


def tool_ids() -> list[str]:
    return list(_load().keys())


def exists(tool_id: str) -> bool:
    return tool_id in _load()


def spec(tool_id: str) -> dict | None:
    return _load().get(tool_id)


def setup_fields(tool_id: str) -> list[dict]:
    return list(_load().get(tool_id, {}).get("setup", []))


def is_write(tool_id: str) -> bool:
    return bool(_load().get(tool_id, {}).get("write_risk", False))


def write_risk_tools(enabled: list[str] | None = None) -> list[str]:
    """The subset of `enabled` (or all) tools that act on the outside world."""
    ids = enabled if enabled is not None else tool_ids()
    return [t for t in ids if exists(t) and is_write(t)]


def manifest(enabled: list[str] | None = None) -> str:
    """A compact, planner-facing description of the available tools.

    Injected into the orchestrator prompt so decomposition can plan tasks that use
    tools. `enabled` restricts to a per-run selection; None means the whole catalog.
    Grouped by capability verb so the planner reads intent, not plumbing.
    """
    ids = enabled if enabled is not None else tool_ids()
    lines: list[str] = []
    for tid in ids:
        s = _load().get(tid)
        if not s:
            continue
        caps = ", ".join(s.get("capabilities", []))
        lines.append(f"- {tid} ({caps}): {s['description']}")
    if not lines:
        return ""
    return (
        "AVAILABLE TOOLS — you may plan subtasks that use these. Delegate a tool-using "
        "subtask to the `tool_user` worker and name the tool in the task description "
        "(e.g. \"use sql_database to append the findings\"). Only these tools exist; do "
        "not invent others:\n" + "\n".join(lines)
    )
