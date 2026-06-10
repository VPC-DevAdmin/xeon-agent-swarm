"""
Run / Step / StepAttempt persistence.

These helpers are called by the swarm pipeline (backend/main.py run_swarm) as
it progresses, so the DB always reflects live run state. They take an
AsyncSession and flush within it; the caller controls the transaction boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.ids import uuid7_str
from backend.db.models import Run, Step, StepAttempt


def _utcnow() -> datetime:
    # Naive UTC — SQLite drops tz info; keep all datetimes naive-UTC so reads and
    # in-Python comparisons don't mix aware/naive.
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Run lifecycle ─────────────────────────────────────────────────────────────

async def create_run(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    job_id: str | None = None,
    trigger: str = "manual",
    query: str,
    config: dict | None = None,
) -> Run:
    run = Run(
        id=run_id or uuid7_str(),
        job_id=job_id,
        trigger=trigger,
        query=query,
        config=config or {},
        status="pending",
    )
    session.add(run)
    await session.flush()
    return run


async def set_run_status(
    session: AsyncSession,
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    langfuse_trace_id: str | None = None,
) -> None:
    run = await session.get(Run, run_id)
    if run is None:
        return
    run.status = status
    if error is not None:
        run.error = error
    if langfuse_trace_id is not None:
        run.langfuse_trace_id = langfuse_trace_id
    if status in ("completed", "failed", "killed"):
        run.completed_at = _utcnow()
    await session.flush()


async def save_task_graph(
    session: AsyncSession, run_id: str, task_graph: dict
) -> None:
    """Persist the orchestrator's decomposition and materialize Step rows."""
    run = await session.get(Run, run_id)
    if run is None:
        return
    run.task_graph = task_graph

    tasks = task_graph.get("tasks", []) if isinstance(task_graph, dict) else []
    for t in tasks:
        step = Step(
            id=uuid7_str(),
            run_id=run_id,
            step_key=t.get("id", ""),
            type=t.get("type", ""),
            objective=t.get("objective") or t.get("description"),
            scope=t.get("scope"),
            success_criteria=t.get("success_criteria"),
            deliverable_format=t.get("deliverable_format"),
            source_constraints=t.get("source_constraints"),
            dependencies=t.get("dependencies", []) or [],
            status="pending",
        )
        session.add(step)
    await session.flush()


async def finalize_run(
    session: AsyncSession,
    run_id: str,
    *,
    document_result: dict | None = None,
    metrics: dict | None = None,
    status: str = "completed",
) -> None:
    run = await session.get(Run, run_id)
    if run is None:
        return
    if document_result is not None:
        run.document_result = document_result
    if metrics is not None:
        run.metrics = metrics
    run.status = status
    run.completed_at = _utcnow()
    await session.flush()


# ── Step lifecycle ────────────────────────────────────────────────────────────

async def _get_step(session: AsyncSession, run_id: str, step_key: str) -> Step | None:
    res = await session.execute(
        select(Step).where(Step.run_id == run_id, Step.step_key == step_key)
    )
    return res.scalar_one_or_none()


async def set_step_status(
    session: AsyncSession,
    run_id: str,
    step_key: str,
    status: str,
    *,
    result: dict | None = None,
    confidence: float | None = None,
    latency_ms: float | None = None,
    total_attempts: int | None = None,
) -> None:
    step = await _get_step(session, run_id, step_key)
    if step is None:
        return
    step.status = status
    if status == "running" and step.started_at is None:
        step.started_at = _utcnow()
    if result is not None:
        step.result = result
    if confidence is not None:
        step.confidence = confidence
    if latency_ms is not None:
        step.latency_ms = latency_ms
    if total_attempts is not None:
        step.total_attempts = total_attempts
    if status in ("completed", "failed", "killed", "rejected_final"):
        step.completed_at = _utcnow()
    await session.flush()


async def record_attempt(
    session: AsyncSession,
    run_id: str,
    step_key: str,
    *,
    attempt_no: int,
    status: str,
    result: dict | None = None,
    validator_verdict: dict | None = None,
    correction_hint: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    model_id: str | None = None,
    latency_ms: float | None = None,
) -> None:
    step = await _get_step(session, run_id, step_key)
    if step is None:
        return
    attempt = StepAttempt(
        id=uuid7_str(),
        step_id=step.id,
        attempt_no=attempt_no,
        status=status,
        result=result,
        validator_verdict=validator_verdict,
        correction_hint=correction_hint,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_id=model_id,
        latency_ms=latency_ms,
        completed_at=_utcnow(),
    )
    session.add(attempt)
    await session.flush()


# ── Reads (for the REST API) ──────────────────────────────────────────────────

async def get_run(session: AsyncSession, run_id: str) -> Run | None:
    res = await session.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.steps).selectinload(Step.attempts))
    )
    return res.scalar_one_or_none()


async def list_runs(
    session: AsyncSession,
    *,
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Run]:
    q = select(Run).order_by(Run.started_at.desc()).limit(limit).offset(offset)
    if job_id is not None:
        q = q.where(Run.job_id == job_id)
    if status is not None:
        q = q.where(Run.status == status)
    res = await session.execute(q)
    return list(res.scalars().all())
