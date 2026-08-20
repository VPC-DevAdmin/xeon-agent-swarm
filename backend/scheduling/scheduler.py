"""
Job scheduler.

A single APScheduler AsyncIOScheduler runs one recurring "scan" job. Each tick
it queries jobs whose next_fire_at is due, claims each one (atomically advancing
next_fire_at so it can't double-fire), enforces the job's overlap_policy, and
launches a run via backend.main.launch_run.

The DB is the source of truth for schedules — APScheduler only provides the
tick. This keeps schedule state durable and replica-safe (claim_due_job takes a
row lock), without registering a per-job APScheduler entry that could drift from
the DB.

overlap_policy:
  skip      — if a run for this job is still active, don't start another
  parallel  — always start, even if a previous run is still going
  queue     — demo: treated like skip (a real queue lands later); logged
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.db.base import get_sessionmaker
from backend.repositories import jobs as jobs_repo

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 30

_scheduler: AsyncIOScheduler | None = None


async def _scan_and_fire() -> None:
    """One scheduler tick: fire every due job, honoring overlap_policy."""
    from backend.main import launch_run  # late import avoids circular dependency

    sm = get_sessionmaker()
    fired = 0
    try:
        async with sm() as session:
            due = await jobs_repo.due_jobs(session)
            await session.commit()
    except Exception as exc:
        logger.warning("scheduler scan failed: %s", exc)
        return

    for job in due:
        try:
            async with sm() as session:
                # Claim first (advances next_fire_at under a row lock).
                claimed = await jobs_repo.claim_due_job(session, job.id)
                if not claimed:
                    await session.commit()
                    continue

                # Overlap policy.
                if job.overlap_policy in ("skip", "queue"):
                    if await jobs_repo.has_active_run(session, job.id):
                        logger.info(
                            "job %s due but a run is active — %s policy, skipping",
                            job.id, job.overlap_policy,
                        )
                        await session.commit()
                        continue
                await session.commit()

            validator_enabled = bool(job.config.get("validator_enabled", True))
            run_id = launch_run(
                job.query,
                validator_enabled=validator_enabled,
                job_id=job.id,
                trigger="schedule",
                enabled_tools=list(job.config.get("enabled_tools", []) or []),
                budget=job.config.get("budget") or None,
            )
            fired += 1
            logger.info("scheduler fired job %s → run %s", job.id, run_id)
        except Exception as exc:
            logger.warning("scheduler failed to fire job %s: %s", job.id, exc)

    if fired:
        logger.info("scheduler tick: fired %d job(s)", fired)


def start_scheduler() -> AsyncIOScheduler:
    """Start the scan loop. Called from the FastAPI lifespan."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scan_and_fire,
        "interval",
        seconds=SCAN_INTERVAL_SECONDS,
        id="job_scan",
        max_instances=1,        # never overlap scans
        coalesce=True,          # collapse missed ticks into one
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Job scheduler started (scan every %ds)", SCAN_INTERVAL_SECONDS)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
