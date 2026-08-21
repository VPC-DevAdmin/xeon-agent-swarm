"""CapacityRun persistence: benchmark history with compare/export support."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.ids import uuid7_str
from backend.db.models import CapacityRun


def _dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


async def save(session: AsyncSession, result: dict, label: str | None = None
               ) -> CapacityRun:
    row = CapacityRun(
        id=uuid7_str(),
        mode=str(result.get("mode") or "unknown"),
        mix=str(result.get("mix") or "custom"),
        verdict=result.get("verdict"),
        capacity_users=result.get("capacity_users"),
        capacity_tiles=result.get("capacity_tiles"),
        duration_s=result.get("duration_s"),
        seed=(result.get("repro") or {}).get("seed"),
        label=label,
        result=result,
        started_at=_dt(result.get("started_at")),
        ended_at=_dt(result.get("ended_at")),
    )
    session.add(row)
    await session.flush()
    return row


def summary(row: CapacityRun) -> dict:
    """List-view shape: everything but the (large) result blob."""
    r = row.result or {}
    return {
        "id": row.id, "mode": row.mode, "mix": row.mix,
        "benchmark_target": r.get("benchmark_target"),
        "inference_backend": r.get("inference_backend"),
        "comparable": r.get("comparable"),
        "verdict": row.verdict,
        "capacity_users": row.capacity_users, "capacity_tiles": row.capacity_tiles,
        "capacity_certified": r.get("capacity_certified"),
        "workflows_per_hour": r.get("workflows_per_hour"),
        "cloud_model_name": (r.get("cloud_model") or {}).get("name"),
        "run_cost_usd": (r.get("cost") or {}).get("run_total_usd"),
        "steady_cost_per_hour": (r.get("cost") or {}).get("steady_cost_per_hour"),
        "circuit_breaker_usd": (r.get("cost") or {}).get("circuit_breaker_usd"),
        "steady_tps": (r.get("steady") or {}).get("tps"),
        "p95_ms": (r.get("steady") or {}).get("p95_ms"),
        "duration_s": row.duration_s, "seed": row.seed, "label": row.label,
        "scenario_fingerprint": (r.get("repro") or {}).get("scenario_fingerprint"),
        "git_commit": (r.get("repro") or {}).get("git_commit"),
        "cache_mode": (r.get("repro") or {}).get("cache_mode"),
        "started_at": row.started_at.isoformat() if row.started_at else None,
    }


async def list_runs(session: AsyncSession, limit: int = 50) -> list[CapacityRun]:
    q = (select(CapacityRun).order_by(CapacityRun.started_at.desc()).limit(limit))
    return list((await session.execute(q)).scalars())


async def get(session: AsyncSession, run_id: str) -> CapacityRun | None:
    return await session.get(CapacityRun, run_id)


async def set_label(session: AsyncSession, run_id: str, label: str | None
                    ) -> CapacityRun | None:
    row = await session.get(CapacityRun, run_id)
    if row is not None:
        row.label = label
        await session.flush()
    return row


async def delete(session: AsyncSession, run_id: str) -> bool:
    row = await session.get(CapacityRun, run_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
