"""
Async evaluation runner.

After a run finalizes, this reads the run's steps from the DB, scores each one
against its deliverable_format rubric (backend/evals/rubrics.py), aggregates,
persists the result into run.metrics["evals"], and broadcasts eval_completed.

Runs as a fire-and-forget asyncio task — it never blocks run completion. It's
read-mostly (one write back to run.metrics), so it's cheap.

The mechanical pass always runs. An optional LLM-judge pass can be layered on
later using the validator/eval model; the structure here leaves room for it
(each step eval is a dict, so adding a `judge` key is non-breaking).
"""
from __future__ import annotations

import logging

from backend.db.base import get_sessionmaker
from backend.evals.rubrics import evaluate_mechanical
from backend.repositories import runs as runs_repo
from backend.schemas.models import EventType, SwarmEvent

logger = logging.getLogger(__name__)


async def evaluate_run(run_id: str, broadcast=None) -> dict | None:
    """Evaluate every step of a completed run; persist + broadcast results."""
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            run = await runs_repo.get_run(session, run_id)
            if run is None:
                return None

            step_evals = []
            for step in run.steps:
                score = evaluate_mechanical(step.deliverable_format, step.result)
                step_evals.append({
                    "step_key": step.step_key,
                    "type": step.type,
                    "deliverable_format": step.deliverable_format,
                    "status": step.status,
                    **score.as_dict(),
                })

            scored = [e for e in step_evals if e["status"] == "completed"]
            avg = (sum(e["score"] for e in scored) / len(scored)) if scored else 0.0
            pass_rate = (
                sum(1 for e in scored if e["passed"]) / len(scored)
                if scored else 0.0
            )
            summary = {
                "avg_score": round(avg, 3),
                "pass_rate": round(pass_rate, 3),
                "steps_evaluated": len(scored),
                "steps": step_evals,
            }

            # Merge into run.metrics without clobbering existing keys.
            metrics = dict(run.metrics or {})
            metrics["evals"] = summary
            run.metrics = metrics
            await session.commit()
    except Exception as exc:
        logger.warning("eval for run %s failed: %s", run_id, exc)
        return None

    logger.info(
        "eval run %s: avg=%.2f pass_rate=%.0f%% (%d steps)",
        run_id, summary["avg_score"], summary["pass_rate"] * 100,
        summary["steps_evaluated"],
    )

    if broadcast is not None:
        try:
            await broadcast(run_id, SwarmEvent(
                event=EventType.eval_completed,
                run_id=run_id,
                payload=summary,
            ))
        except Exception:
            pass

    return summary
