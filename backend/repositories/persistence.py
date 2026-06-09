"""
Pipeline persistence facade.

Thin wrappers that open a short-lived AsyncSession around each repository call,
so the swarm pipeline (backend/main.py) can persist run state without managing
sessions itself. Each function is self-contained and commits on success.

All functions are best-effort: a persistence failure logs a warning but never
aborts the run. The in-memory _run_results cache remains the source of truth
for the live dashboard; the DB is the durable record that survives restarts and
feeds the REST API.
"""
from __future__ import annotations

import logging

from backend.db.base import get_sessionmaker
from backend.repositories import jobs as jobs_repo
from backend.repositories import runs as runs_repo

logger = logging.getLogger(__name__)


async def _run(coro_factory, label: str):
    """Open a session, run the repo coroutine, commit. Swallow + log errors."""
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            result = await coro_factory(session)
            await session.commit()
            return result
    except Exception as exc:  # never let persistence break a run
        logger.warning("persistence[%s] failed: %s", label, exc)
        return None


async def create_run(run_id, query, *, job_id=None, trigger="manual", config=None):
    return await _run(
        lambda s: runs_repo.create_run(
            s, run_id=run_id, job_id=job_id, trigger=trigger,
            query=query, config=config or {},
        ),
        "create_run",
    )


async def set_run_status(run_id, status, **kw):
    return await _run(
        lambda s: runs_repo.set_run_status(s, run_id, status, **kw),
        f"set_run_status:{status}",
    )


async def save_task_graph(run_id, task_graph: dict):
    return await _run(
        lambda s: runs_repo.save_task_graph(s, run_id, task_graph),
        "save_task_graph",
    )


async def set_step_status(run_id, step_key, status, **kw):
    return await _run(
        lambda s: runs_repo.set_step_status(s, run_id, step_key, status, **kw),
        f"set_step_status:{step_key}:{status}",
    )


async def record_attempt(run_id, step_key, **kw):
    return await _run(
        lambda s: runs_repo.record_attempt(s, run_id, step_key, **kw),
        f"record_attempt:{step_key}",
    )


async def finalize_run(run_id, *, document_result=None, metrics=None, status="completed"):
    return await _run(
        lambda s: runs_repo.finalize_run(
            s, run_id, document_result=document_result,
            metrics=metrics, status=status,
        ),
        "finalize_run",
    )


async def set_job_last_run(job_id, run_id):
    """Record a job's most recent run_id (schedule advancement is the
    scheduler's responsibility via claim_due_job)."""
    return await _run(
        lambda s: jobs_repo.set_last_run(s, job_id, run_id),
        "set_job_last_run",
    )
