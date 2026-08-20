"""
AgentDefinition persistence: CRUD + versioning + clone + schedule sync.

Versioning: every update bumps `version` and snapshots the PRIOR configurable
state into `history` (last 10 kept) so a definition's evolution is auditable
and a bad edit can be recovered by hand.

Schedule sync: a definition with schedule_cron keeps a linked Job whose query
is the definition's instructions and whose config carries the definition's
tools/validator/budgets — the scheduler already honors those fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.ids import uuid7_str
from backend.db.models import AgentDefinition
from backend.repositories import jobs as jobs_repo

# The fields captured in a version snapshot / clone.
_CONFIG_FIELDS = ("name", "icon", "purpose", "instructions", "enabled_tools",
                  "plan_approval", "validator_enabled", "budgets",
                  "session_policy", "slo", "schedule_cron", "schedule_tz")


def _snapshot(d: AgentDefinition) -> dict:
    return {f: getattr(d, f) for f in _CONFIG_FIELDS}


async def create(session: AsyncSession, **fields) -> AgentDefinition:
    d = AgentDefinition(id=uuid7_str(), **fields)
    session.add(d)
    await session.flush()
    await _sync_job(session, d)
    return d


async def get(session: AsyncSession, def_id: str) -> AgentDefinition | None:
    return await session.get(AgentDefinition, def_id)


async def get_by_name(session: AsyncSession, name: str) -> AgentDefinition | None:
    res = await session.execute(
        select(AgentDefinition).where(AgentDefinition.name == name))
    return res.scalar_one_or_none()


async def list_defs(session: AsyncSession, *, include_archived: bool = False
                    ) -> list[AgentDefinition]:
    q = select(AgentDefinition).order_by(AgentDefinition.created_at.desc())
    if not include_archived:
        q = q.where(AgentDefinition.status == "active")
    return list((await session.execute(q)).scalars())


async def update(session: AsyncSession, def_id: str, changes: dict
                 ) -> AgentDefinition | None:
    d = await get(session, def_id)
    if d is None:
        return None
    hist = list(d.history or [])
    hist.append({"version": d.version,
                 "ts": datetime.now(timezone.utc).isoformat(),
                 "snapshot": _snapshot(d)})
    d.history = hist[-10:]
    for k, v in changes.items():
        if k in _CONFIG_FIELDS:
            setattr(d, k, v)
    d.version += 1
    await session.flush()
    await _sync_job(session, d)
    return d


async def clone(session: AsyncSession, def_id: str) -> AgentDefinition | None:
    src = await get(session, def_id)
    if src is None:
        return None
    fields = _snapshot(src)
    base = fields.pop("name")
    name = f"{base} (copy)"
    n = 2
    while await get_by_name(session, name) is not None:
        name = f"{base} (copy {n})"
        n += 1
    # A clone starts a fresh lineage — no schedule (avoid surprise double-fires).
    fields.pop("schedule_cron", None)
    d = AgentDefinition(id=uuid7_str(), name=name, **fields)
    session.add(d)
    await session.flush()
    return d


async def archive(session: AsyncSession, def_id: str) -> AgentDefinition | None:
    d = await get(session, def_id)
    if d is None:
        return None
    d.status = "archived"
    if d.job_id:
        await jobs_repo.set_job_status(session, d.job_id, "archived")
        d.job_id = None
    await session.flush()
    return d


async def _sync_job(session: AsyncSession, d: AgentDefinition) -> None:
    """Keep the linked Job consistent with the definition's schedule."""
    config = {
        "agent_definition_id": d.id,
        "validator_enabled": d.validator_enabled,
        "enabled_tools": list(d.enabled_tools or []),
        "budget": d.budgets or None,
    }
    if d.schedule_cron:
        if d.job_id and await jobs_repo.get_job(session, d.job_id):
            await jobs_repo.update_job(
                session, d.job_id, name=f"[agent] {d.name}",
                query=d.instructions, config=config,
                schedule_cron=d.schedule_cron, schedule_tz=d.schedule_tz)
        else:
            job = await jobs_repo.create_job(
                session, name=f"[agent] {d.name}", query=d.instructions,
                description=d.purpose, config=config,
                schedule_cron=d.schedule_cron, schedule_tz=d.schedule_tz)
            d.job_id = job.id
    elif d.job_id:
        await jobs_repo.set_job_status(session, d.job_id, "archived")
        d.job_id = None
    await session.flush()
