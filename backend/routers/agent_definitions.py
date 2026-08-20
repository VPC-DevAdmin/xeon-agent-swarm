"""
/agent-definitions — persistent, configured agents (the product object).

Terminology (docs/standards.md): an *agent definition* is the persistent
configuration; a *run* is one execution of it; *workers* are the runtime
specialist subagents a run spawns. Definitions can be run once (test console),
scheduled (kept in sync with a linked Job), cloned, versioned, and assigned to
the capacity benchmark's e2e mix.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from backend.repositories import agent_defs as repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent-definitions", tags=["agent-definitions"])


class DefinitionBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    icon: str = Field("🤖", max_length=16)
    purpose: Optional[str] = Field(None, max_length=500)
    instructions: str = Field(..., min_length=1, max_length=10_000)
    enabled_tools: list[str] = Field(default_factory=list)
    plan_approval: bool = False
    validator_enabled: bool = True
    budgets: Optional[dict] = None          # {max_subagents, max_tool_hops, max_total_tokens}
    session_policy: Optional[dict] = None   # {turns, context_cap}
    slo: Optional[dict] = None              # {p95_ms}
    schedule_cron: Optional[str] = None
    schedule_tz: str = "UTC"


class DefinitionPatch(BaseModel):
    # every field optional; only provided keys change (and bump the version)
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    icon: Optional[str] = Field(None, max_length=16)
    purpose: Optional[str] = Field(None, max_length=500)
    instructions: Optional[str] = Field(None, min_length=1, max_length=10_000)
    enabled_tools: Optional[list[str]] = None
    plan_approval: Optional[bool] = None
    validator_enabled: Optional[bool] = None
    budgets: Optional[dict] = None
    session_policy: Optional[dict] = None
    slo: Optional[dict] = None
    schedule_cron: Optional[str] = None
    clear_schedule: bool = False
    schedule_tz: Optional[str] = None


class RunOnceBody(BaseModel):
    input: Optional[str] = Field(None, max_length=4000)  # appended to instructions


def _out(d) -> dict:
    return {
        "id": d.id, "name": d.name, "icon": d.icon, "purpose": d.purpose,
        "instructions": d.instructions, "enabled_tools": d.enabled_tools or [],
        "plan_approval": d.plan_approval, "validator_enabled": d.validator_enabled,
        "budgets": d.budgets, "session_policy": d.session_policy, "slo": d.slo,
        "schedule_cron": d.schedule_cron, "schedule_tz": d.schedule_tz,
        "job_id": d.job_id, "version": d.version, "status": d.status,
        "history": d.history or [],
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


@router.post("")
async def create_definition(body: DefinitionBody,
                            session: AsyncSession = Depends(get_session)) -> dict:
    if await repo.get_by_name(session, body.name):
        raise HTTPException(409, f"agent definition '{body.name}' already exists")
    d = await repo.create(session, **body.model_dump())
    await session.commit()
    await session.refresh(d)   # load server-generated columns eagerly
    return _out(d)


@router.get("")
async def list_definitions(all: bool = Query(False),
                           session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [_out(d) for d in await repo.list_defs(session, include_archived=all)]


@router.get("/{def_id}")
async def get_definition(def_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    d = await repo.get(session, def_id)
    if d is None:
        raise HTTPException(404, "agent definition not found")
    return _out(d)


@router.patch("/{def_id}")
async def update_definition(def_id: str, body: DefinitionPatch,
                            session: AsyncSession = Depends(get_session)) -> dict:
    changes = {k: v for k, v in body.model_dump().items()
               if v is not None and k != "clear_schedule"}
    if body.clear_schedule:
        changes["schedule_cron"] = None
    d = await repo.update(session, def_id, changes)
    if d is None:
        raise HTTPException(404, "agent definition not found")
    await session.commit()
    await session.refresh(d)
    return _out(d)


@router.post("/{def_id}/clone")
async def clone_definition(def_id: str,
                           session: AsyncSession = Depends(get_session)) -> dict:
    d = await repo.clone(session, def_id)
    if d is None:
        raise HTTPException(404, "agent definition not found")
    await session.commit()
    await session.refresh(d)
    return _out(d)


@router.post("/{def_id}/archive")
async def archive_definition(def_id: str,
                             session: AsyncSession = Depends(get_session)) -> dict:
    d = await repo.archive(session, def_id)
    if d is None:
        raise HTTPException(404, "agent definition not found")
    await session.commit()
    await session.refresh(d)
    return _out(d)


@router.post("/{def_id}/run")
async def run_definition_once(def_id: str, body: RunOnceBody,
                              session: AsyncSession = Depends(get_session)) -> dict:
    """The test console: one execution with the definition's full policy —
    tools, plan approval, validator, budgets."""
    d = await repo.get(session, def_id)
    if d is None:
        raise HTTPException(404, "agent definition not found")
    if d.status != "active":
        raise HTTPException(409, "agent definition is archived")
    from backend.main import launch_run
    query = d.instructions if not body.input else f"{d.instructions}\n\nInput: {body.input}"
    run_id = launch_run(
        query,
        validator_enabled=d.validator_enabled,
        trigger="manual",
        plan_approval=d.plan_approval,
        enabled_tools=list(d.enabled_tools or []),
        budget=d.budgets or None,
    )
    logger.info("agent definition %s (%s v%s) test run -> %s",
                d.id, d.name, d.version, run_id)
    return {"run_id": run_id, "definition_id": d.id, "version": d.version}
