"""
/runs — list and inspect run history (durable, from the DB).

Live run state (in-progress dashboard) still comes from the WebSocket and the
in-memory cache via /run/{run_id} in main.py. These endpoints serve the durable
record: history listing, full step/attempt detail, and kill.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from backend.repositories import runs as runs_repo
from backend.schemas.api import RunSummary

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunSummary])
async def list_runs(
    job_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    runs = await runs_repo.list_runs(
        session, job_id=job_id, status=status, limit=limit, offset=offset
    )
    return [RunSummary.from_orm_run(r) for r in runs]


@router.get("/{run_id}")
async def get_run_detail(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await runs_repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    # Reuse the serializer in main to keep one shape.
    from backend.main import _run_to_dict
    return _run_to_dict(run)


@router.post("/{run_id}/kill")
async def kill_run(run_id: str, session: AsyncSession = Depends(get_session)):
    """Cancel all in-flight steps of a run and mark it killed.

    Cancels the asyncio tasks tracked in main._running_tasks, then records the
    durable status. Steps that already completed keep their results.
    """
    from backend.main import _running_tasks
    from backend.repositories import persistence as db

    tasks = _running_tasks.get(run_id, {})
    cancelled = 0
    for t in list(tasks.values()):
        if not t.done():
            t.cancel()
            cancelled += 1
    await db.set_run_status(run_id, "killed")
    return {"status": "killed", "run_id": run_id, "cancelled_steps": cancelled}
