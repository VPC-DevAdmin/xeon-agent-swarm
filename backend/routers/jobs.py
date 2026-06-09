"""
/jobs — CRUD + lifecycle for user-defined orchestration units.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from backend.repositories import jobs as jobs_repo
from backend.schemas.api import JobCreate, JobOut, JobUpdate

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut)
async def create_job(body: JobCreate, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.create_job(
        session,
        name=body.name,
        query=body.query,
        description=body.description,
        config=body.config,
        schedule_cron=body.schedule_cron,
        schedule_tz=body.schedule_tz,
        overlap_policy=body.overlap_policy,
        owner=body.owner,
        connector_ids=body.connector_ids,
    )
    job = await jobs_repo.get_job(session, job.id)  # reload with connectors
    return JobOut.from_orm_job(job)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    status: str | None = Query(None),
    owner: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    jobs = await jobs_repo.list_jobs(
        session, status=status, owner=owner, search=search,
        limit=limit, offset=offset,
    )
    # connectors aren't eager-loaded in list; expose ids best-effort
    return [JobOut.from_orm_job(j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.get_job(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.from_orm_job(job)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: str, body: JobUpdate, session: AsyncSession = Depends(get_session)
):
    # clear_schedule=True means set cron to None; otherwise only touch cron if
    # a value was provided (sentinel ... = leave unchanged).
    cron_arg: object = ...
    if body.clear_schedule:
        cron_arg = None
    elif body.schedule_cron is not None:
        cron_arg = body.schedule_cron

    job = await jobs_repo.update_job(
        session, job_id,
        name=body.name,
        description=body.description,
        query=body.query,
        config=body.config,
        schedule_cron=cron_arg,
        schedule_tz=body.schedule_tz,
        overlap_policy=body.overlap_policy,
    )
    if job is None:
        raise HTTPException(404, "job not found")
    job = await jobs_repo.get_job(session, job_id)
    return JobOut.from_orm_job(job)


@router.post("/{job_id}/pause", response_model=JobOut)
async def pause_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.set_job_status(session, job_id, "paused")
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.from_orm_job(await jobs_repo.get_job(session, job_id))


@router.post("/{job_id}/resume", response_model=JobOut)
async def resume_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.set_job_status(session, job_id, "active")
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.from_orm_job(await jobs_repo.get_job(session, job_id))


@router.post("/{job_id}/archive", response_model=JobOut)
async def archive_job(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.set_job_status(session, job_id, "archived")
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.from_orm_job(await jobs_repo.get_job(session, job_id))


@router.post("/{job_id}/run-now")
async def run_now(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.get_job(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # Late import to avoid a circular dependency with backend.main.
    from backend.main import launch_run
    run_id = launch_run(
        job.query,
        validator_enabled=bool(job.config.get("validator_enabled", True)),
        job_id=job.id,
        trigger="manual",
    )
    return {"run_id": run_id, "job_id": job_id}
