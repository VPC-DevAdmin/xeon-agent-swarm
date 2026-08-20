"""
/capacity — the built-in capacity tester ("system speed test").

Five fixed agent scenarios, three target modes (local SGLang engine, simulated
remote with bell-curve latency, real cloud endpoint), a ramp that adds virtual
users until the system saturates, and live telemetry for the UI.

One test at a time. remote_real requires explicit env configuration AND
confirm=true, and rides a hard request budget — this endpoint must never be able
to spray a cloud API by accident.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.base import get_session
from pydantic import BaseModel, Field

from backend.capacity import engine as engine_mgr
from backend.capacity.client import remote_real_configured, REMOTE_MODEL
from backend.capacity.controller import CapacityTest, DEFAULTS
from backend.capacity.scenarios import scenario_list

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/capacity", tags=["capacity"])

_current: CapacityTest | None = None
_task: asyncio.Task | None = None
_last_result: dict | None = None
_last_engine_start: float = 0.0
_ENGINE_COOLDOWN_S = 30.0


def _check_control_token(x_capacity_token: str | None = Header(None)) -> None:
    """Optional hardening for exposed deployments: when CAPACITY_CONTROL_TOKEN
    is set, the control endpoints (start test / start engine) require the
    X-Capacity-Token header. Unset (the default) => no extra gate; Cloudflare
    Access remains the outer wall. NOTE: the UI does not send this header —
    setting the token makes control API-only by design."""
    expected = os.getenv("CAPACITY_CONTROL_TOKEN", "").strip()
    if expected and x_capacity_token != expected:
        raise HTTPException(401, "X-Capacity-Token required")


class StartBody(BaseModel):
    mode: str = Field(..., pattern="^(local|remote_mock|remote_real|e2e)$")
    # "tile": ramp complete reference tiles (comparable benchmark, default).
    # "custom": round-robin over `scenarios` (diagnosis; non-comparable).
    mix: str = Field("tile", pattern="^(tile|custom)$")
    scenarios: list[str] = Field(default_factory=list)  # custom mix only
    mock_ms: float | None = Field(None, ge=100, le=60_000)
    mock_sigma: float | None = Field(None, ge=0, le=20_000)
    max_users: int | None = Field(None, ge=1, le=512)
    step_interval_s: float | None = Field(None, ge=3, le=120)
    step_users: int | None = Field(None, ge=1, le=8)
    agent_definitions: list[str] = Field(default_factory=list)  # e2e custom mix
    seed: int | None = Field(None, ge=0)             # reproducible corpus + report
    cache_mode: str = Field("warm", pattern="^(warm|cold)$")
    warmup_s: float | None = Field(None, ge=0, le=120)
    confirm_real: bool = False  # must be true for remote_real


@router.get("/scenarios")
async def get_scenarios() -> dict:
    from backend.capacity.scenarios import load_tile, load_e2e_workflows, load_e2e_tile
    return {"scenarios": scenario_list(), "tile": load_tile(),
            "e2e_workflows": [{"id": wid, **w} for wid, w in load_e2e_workflows().items()],
            "e2e_tile": load_e2e_tile(), "defaults": DEFAULTS}


@router.get("/engine")
async def get_engine() -> dict:
    return {**engine_mgr.status(), **(await engine_mgr.probe()),
            "remote_real": {"configured": remote_real_configured(),
                            "model": REMOTE_MODEL or None}}


@router.post("/engine/start")
async def start_engine(_: None = Depends(_check_control_token)) -> dict:
    """Cooldown-protected: engine bring-up launches docker / downloads models —
    repeated clicks or scripted spam must not stack attempts."""
    global _last_engine_start
    now = time.monotonic()
    if now - _last_engine_start < _ENGINE_COOLDOWN_S:
        return {"started": False,
                "reason": f"cooldown — retry in {int(_ENGINE_COOLDOWN_S - (now - _last_engine_start))}s"}
    out = await engine_mgr.start()
    if out.get("started"):
        _last_engine_start = now
    return out


@router.post("/start")
async def start_test(body: StartBody,
                     _: None = Depends(_check_control_token)) -> dict:
    global _current, _task
    if _current is not None and _current.status()["active"]:
        raise HTTPException(409, "a capacity test is already running")
    if body.mode == "remote_real":
        if not remote_real_configured():
            raise HTTPException(400, "remote_real is not configured — set "
                                      "CAPACITY_REMOTE_BASE_URL / _MODEL / _API_KEY")
        if not body.confirm_real:
            raise HTTPException(400, "remote_real spends real API credits — "
                                      "pass confirm_real=true")
    if body.mode == "local" and not (await engine_mgr.probe())["serving"]:
        raise HTTPException(409, "local engine is not serving — start it from the "
                                  "Capacity tab (or POST /capacity/engine/start)")
    cfg = {"mock_ms": body.mock_ms, "mock_sigma": body.mock_sigma,
           "max_users": body.max_users, "step_interval_s": body.step_interval_s,
           "step_users": body.step_users, "seed": body.seed,
           "cache_mode": body.cache_mode, "warmup_s": body.warmup_s}
    if body.mode == "e2e":
        # Whole workflows are the unit: slower cadence, smaller scale, fewer
        # samples needed to certify a rung.
        cfg.setdefault("step_interval_s", body.step_interval_s)
        cfg["step_interval_s"] = body.step_interval_s or 30.0
        cfg["max_users"] = body.max_users or 12
        cfg["min_samples"] = 2
    extra_workflows: dict = {}
    if body.mode == "e2e" and body.agent_definitions:
        from backend.db.base import get_sessionmaker
        from backend.repositories import agent_defs as defs_repo
        sm = get_sessionmaker()
        async with sm() as session:
            for def_id in body.agent_definitions[:12]:
                d = await defs_repo.get(session, def_id)
                if d and d.status == "active":
                    extra_workflows[f"def:{d.id[:8]}"] = {
                        "name": f"{d.icon} {d.name} (v{d.version})",
                        "query": d.instructions,
                        "think_ms": 3000,
                        "enabled_tools": list(d.enabled_tools or []),
                        "validator_enabled": d.validator_enabled,
                        "budgets": d.budgets,
                    }
        if extra_workflows and body.mix == "tile":
            raise HTTPException(400, "agent definitions run in the custom mix — "
                                      "the reference tile stays locked for comparability")
        if extra_workflows:
            body.scenarios = list(body.scenarios) + list(extra_workflows)
    _current = CapacityTest(body.mode, body.scenarios, cfg, mix=body.mix,
                            extra_workflows=extra_workflows)
    _task = asyncio.create_task(_run_and_keep(_current))
    logger.info("capacity test started: mode=%s scenarios=%s", body.mode,
                _current.scenario_ids)
    return {"started": True, "mode": body.mode, "mix": _current.mix,
            "scenarios": _current.scenario_ids}


async def _run_and_keep(test: CapacityTest):
    global _last_result
    await test.run()
    if test.result:
        _last_result = test.result


@router.post("/stop")
async def stop_test() -> dict:
    if _current is None or not _current.status()["active"]:
        raise HTTPException(409, "no capacity test is running")
    _current.stop()
    return {"stopping": True}


@router.get("/status")
async def get_status() -> dict:
    if _current is None:
        return {"active": False, "phase": "idle", "result": _last_result}
    return _current.status()


# ── benchmark history (DB-persisted; survives restarts) ──────────────────────

@router.get("/history")
async def history(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    from backend.repositories import capacity_runs as caps_repo
    rows = await caps_repo.list_runs(session, limit=min(limit, 200))
    return [caps_repo.summary(r) for r in rows]


@router.get("/history/{run_id}")
async def history_get(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    from backend.repositories import capacity_runs as caps_repo
    row = await caps_repo.get(session, run_id)
    if row is None:
        raise HTTPException(404, "capacity run not found")
    return {**caps_repo.summary(row), "result": row.result}


@router.patch("/history/{run_id}")
async def history_label(run_id: str, body: dict,
                        session: AsyncSession = Depends(get_session)) -> dict:
    from backend.repositories import capacity_runs as caps_repo
    row = await caps_repo.set_label(session, run_id,
                                    (body or {}).get("label") or None)
    if row is None:
        raise HTTPException(404, "capacity run not found")
    await session.commit()
    return caps_repo.summary(row)


@router.delete("/history/{run_id}")
async def history_delete(run_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    from backend.repositories import capacity_runs as caps_repo
    if not await caps_repo.delete(session, run_id):
        raise HTTPException(404, "capacity run not found")
    await session.commit()
    return {"deleted": run_id}


@router.get("/history/{run_id}/export")
async def history_export(run_id: str, session: AsyncSession = Depends(get_session)):
    """The full result blob as a download — repro block and all."""
    from backend.repositories import capacity_runs as caps_repo
    row = await caps_repo.get(session, run_id)
    if row is None:
        raise HTTPException(404, "capacity run not found")
    stamp = row.started_at.strftime("%Y%m%d-%H%M%S") if row.started_at else row.id[:8]
    return JSONResponse(row.result, headers={
        "Content-Disposition":
            f'attachment; filename="capacity-{stamp}-{row.mode}-{row.mix}.json"'})
