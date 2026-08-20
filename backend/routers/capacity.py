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

from fastapi import APIRouter, HTTPException
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
async def start_engine() -> dict:
    return await engine_mgr.start()


@router.post("/start")
async def start_test(body: StartBody) -> dict:
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
    _current = CapacityTest(body.mode, body.scenarios, cfg, mix=body.mix)
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
