"""
Job persistence — user-defined orchestration units (query + schedule + config).
"""
from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.ids import uuid7_str
from backend.db.models import Job, JobConnector


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_next_fire(cron: str | None, tz: str = "UTC", base: datetime | None = None) -> datetime | None:
    """Next fire time for a cron expression, or None if not scheduled.

    croniter handles the cron arithmetic. We keep tz on the Job and compute in
    UTC for storage; tz-aware display is the UI's concern.
    """
    if not cron:
        return None
    base = base or _utcnow()
    try:
        itr = croniter(cron, base)
        return itr.get_next(datetime)
    except (ValueError, KeyError):
        return None


async def create_job(
    session: AsyncSession,
    *,
    name: str,
    query: str,
    description: str | None = None,
    config: dict | None = None,
    schedule_cron: str | None = None,
    schedule_tz: str = "UTC",
    overlap_policy: str = "skip",
    owner: str | None = None,
    connector_ids: list[str] | None = None,
) -> Job:
    job = Job(
        id=uuid7_str(),
        name=name,
        description=description,
        query=query,
        config=config or {},
        schedule_cron=schedule_cron,
        schedule_tz=schedule_tz,
        overlap_policy=overlap_policy,
        status="active",
        owner=owner,
        next_fire_at=compute_next_fire(schedule_cron, schedule_tz),
    )
    session.add(job)
    await session.flush()

    for cid in connector_ids or []:
        session.add(JobConnector(job_id=job.id, connector_id=cid))
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: str) -> Job | None:
    res = await session.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.connectors))
    )
    return res.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    *,
    status: str | None = None,
    owner: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    q = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    if status is not None:
        q = q.where(Job.status == status)
    else:
        q = q.where(Job.status != "archived")  # hide archived by default
    if owner is not None:
        q = q.where(Job.owner == owner)
    if search:
        like = f"%{search}%"
        q = q.where(Job.name.ilike(like) | Job.query.ilike(like))
    res = await session.execute(q)
    return list(res.scalars().all())


async def update_job(
    session: AsyncSession,
    job_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    query: str | None = None,
    config: dict | None = None,
    schedule_cron: str | None = ...,  # sentinel: None means "clear schedule"
    schedule_tz: str | None = None,
    overlap_policy: str | None = None,
) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        return None
    if name is not None:
        job.name = name
    if description is not None:
        job.description = description
    if query is not None:
        job.query = query
    if config is not None:
        job.config = config
    if schedule_tz is not None:
        job.schedule_tz = schedule_tz
    if overlap_policy is not None:
        job.overlap_policy = overlap_policy
    if schedule_cron is not ...:
        job.schedule_cron = schedule_cron
        job.next_fire_at = compute_next_fire(schedule_cron, job.schedule_tz)
    await session.flush()
    return job


async def set_job_status(session: AsyncSession, job_id: str, status: str) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        return None
    job.status = status
    if status == "archived":
        job.archived_at = _utcnow()
        job.next_fire_at = None
    elif status == "active" and job.schedule_cron:
        job.next_fire_at = compute_next_fire(job.schedule_cron, job.schedule_tz)
    elif status == "paused":
        job.next_fire_at = None
    await session.flush()
    return job


async def mark_fired(session: AsyncSession, job_id: str, run_id: str) -> None:
    """After a schedule fires: bump next_fire_at and record last_run_id."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.last_run_id = run_id
    job.next_fire_at = compute_next_fire(job.schedule_cron, job.schedule_tz)
    await session.flush()


async def due_jobs(session: AsyncSession, *, now: datetime | None = None) -> list[Job]:
    """Active jobs whose next_fire_at is in the past (the scheduler's scan)."""
    now = now or _utcnow()
    res = await session.execute(
        select(Job).where(
            Job.status == "active",
            Job.next_fire_at.is_not(None),
            Job.next_fire_at <= now,
        )
    )
    return list(res.scalars().all())
