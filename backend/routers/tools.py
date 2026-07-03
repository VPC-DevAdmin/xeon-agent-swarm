"""
/tools — the curated tool catalog and how to set each one up.

The catalog (config/tool_catalog.yaml) is the fixed, demo-able set of tools the
planner can compose with. This endpoint powers the Tools gallery: every tool with
its category, capabilities, description, the fields needed to configure it, and
whether it's configured yet (a Connector named after the tool_id exists with its
required secrets set).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from backend.repositories import connectors as conn_repo
from backend.agents import tool_catalog

router = APIRouter(prefix="/tools", tags=["tools"])


def _required_secret_fields(setup: list[dict]) -> list[str]:
    return [f["field"] for f in setup if f.get("secret")]


@router.get("")
async def list_tools(session: AsyncSession = Depends(get_session)) -> dict:
    """The catalog + per-tool configured status.

    Returns:
      categories: ordered category ids
      tools:      [{id, name, category, description, capabilities, backing,
                    write_risk, setup, configured}]
    """
    # A configured tool = an active Connector named tool_id with all required
    # secret fields present. One query, then match in memory.
    connectors = await conn_repo.list_connectors(session, kind="tool", status="active")
    have: dict[str, set[str]] = {}
    for c in connectors:
        full = await conn_repo.get_connector(session, c.id)
        have[c.name] = set(conn_repo.secret_field_names(full))

    tools = []
    for t in tool_catalog.catalog_list():
        required = set(_required_secret_fields(t.get("setup", [])))
        present = have.get(t["id"])
        configured = present is not None and required.issubset(present)
        tools.append({**t, "configured": configured})

    return {"categories": tool_catalog.CATEGORY_ORDER, "tools": tools}
