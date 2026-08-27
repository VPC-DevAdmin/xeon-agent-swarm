"""
Pipeline persistence facade — a BATCHED single-writer per process.

Every write is enqueued (FIFO, so run -> step -> attempt ordering holds) and a
background writer flushes up to _BATCH_MAX ops per transaction on ONE
connection. This is what lets thousands of concurrent agent sessions share a
database: per-op connection checkouts turned 4,500 ops/s into a pool storm
(measured: 'QueuePool limit reached' tails ended the ramp at 375 sessions),
while batching needs ~2 connections per process at any session count.

All writes are best-effort and fire-and-forget: a failure logs a warning but
never aborts a run. `barrier()` awaits everything enqueued so far — call it
before reading back state you just wrote (e.g. the executor's completion
callback). Reads are unaffected; they may lag writes by <= the flush interval.
"""
from __future__ import annotations

import asyncio
import logging

from backend.db.base import get_sessionmaker
from backend.repositories import jobs as jobs_repo
from backend.repositories import runs as runs_repo

logger = logging.getLogger(__name__)

_BATCH_MAX = 200
_FLUSH_S = 0.05
_perm_failures = 0        # writes abandoned after retry: harness integrity signal
_queue: asyncio.Queue | None = None
_writer: asyncio.Task | None = None


def _ensure_writer() -> asyncio.Queue:
    global _queue, _writer
    if _queue is None or _writer is None or _writer.done():
        _queue = _queue or asyncio.Queue()
        _writer = asyncio.get_event_loop().create_task(_writer_loop())
        _writer.add_done_callback(
            lambda t: (not t.cancelled() and t.exception()) and logger.error(
                "persistence writer died: %r", t.exception()))
    return _queue


async def _writer_loop():
    loop = asyncio.get_event_loop()
    while True:
        batch = [await _queue.get()]
        deadline = loop.time() + _FLUSH_S
        while len(batch) < _BATCH_MAX:
            timeout = deadline - loop.time()
            if timeout <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(_queue.get(), timeout))
            except asyncio.TimeoutError:
                break
        await _flush(batch)


async def _flush(batch):
    global _perm_failures
    ops = [(f, label) for f, label, _fut in batch if f is not None]
    if ops:
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                for f, _label in ops:
                    await f(session)
                await session.commit()
        except Exception:  # noqa: BLE001 — isolate the poison op, keep the rest
            for f, label in ops:
                for attempt, delay in enumerate((0.0, 0.1, 0.4)):
                    if delay:
                        await asyncio.sleep(delay)
                    try:
                        sm = get_sessionmaker()
                        async with sm() as session:
                            await f(session)
                            await session.commit()
                        break
                    except Exception as exc:  # noqa: BLE001
                        transient = ("locked" in str(exc).lower()
                                     or "deadlock" in str(exc).lower())
                        if transient and attempt < 2:
                            continue
                        _perm_failures += 1
                        logger.warning("persistence[%s] failed permanently: %s",
                                       label, exc)
                        break
    for *_, fut in batch:
        if fut is not None and not fut.done():
            fut.set_result(None)


def failure_count() -> int:
    """Writes abandoned after retry since process start.

    A benchmark cannot certify a level whose durable record is incomplete, so
    the controller reconciles this counter across every process before it
    publishes a result."""
    return _perm_failures


async def barrier():
    """Resolve once everything enqueued before this call has been flushed.

    The barrier reports flush completion, not write success. Callers that need
    integrity read failure_count()."""
    q = _ensure_writer()
    fut = asyncio.get_event_loop().create_future()
    q.put_nowait((None, "barrier", fut))
    await fut


async def _run(coro_factory, label: str):
    """Enqueue a write for the batched writer. Fire-and-forget (returns None)."""
    _ensure_writer().put_nowait((coro_factory, label, None))
    return None


async def audit_bench(key: str):
    """Benchmark tool record — one durable AuditLog row through the writer."""
    from backend.db.models import AuditLog

    async def _op(session):
        session.add(AuditLog(action="bench.record", detail={"key": key[:120]}))

    return await _run(_op, "bench.record")


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


async def create_step(run_id, *, step_key, type, **kw):
    return await _run(
        lambda s: runs_repo.create_step(s, run_id, step_key=step_key, type=type, **kw),
        f"create_step:{step_key}",
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


async def record_validation(run_id, step_key, *, level, verdict, **kw):
    return await _run(
        lambda s: runs_repo.record_validation(
            s, run_id, step_key, level=level, verdict=verdict, **kw),
        f"record_validation:{step_key}:{level}",
    )


async def finalize_run(run_id, *, document_result=None, metrics=None,
                       status="completed", error=None):
    return await _run(
        lambda s: runs_repo.finalize_run(
            s, run_id, document_result=document_result,
            metrics=metrics, status=status, error=error,
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
