"""/capacity — capacity tests with an explicit target and inference backend.

The target says which boundary is under test: the agent host/orchestrator, an
integrated local agent node, or the inference engine alone.  The inference
backend says where model calls go.  Keeping those dimensions separate prevents
a synthetic remote latency test from being mistaken for agent-host capacity.

One test at a time. Cloud runs require explicit confirmation and a dollar
circuit breaker; the workload ramp itself has no session or duration ceiling.
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
from backend.capacity.client import LOCAL_BASE, LOCAL_MODEL
from backend.capacity.models import catalog_for_api, resolve_endpoint
from backend.capacity.controller import CapacityTest, DEFAULTS
from backend.capacity import repeat as rpt
from backend.capacity.repeat import RepeatSet
from backend.capacity.scenarios import scenario_list
from backend.capacity.scenarios import arrival_schedule as scen_arrival_schedule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/capacity", tags=["capacity"])

_current: CapacityTest | None = None
_task: asyncio.Task | None = None
_last_result: dict | None = None
_repeat: RepeatSet | None = None
_repeat_task: asyncio.Task | None = None
_last_repeat: dict | None = None
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
    benchmark_target: str | None = Field(
        None, pattern="^(agent_host|integrated_node|inference_engine)$")
    inference_backend: str | None = Field(
        None, pattern="^(local|remote_mock|remote_real)$")
    # Compatibility for saved clients. New clients send the two fields above.
    mode: str | None = Field(None, pattern="^(local|remote_mock|remote_real|e2e)$")
    # "tile": ramp complete reference tiles (comparable benchmark, default).
    # "custom": round-robin over `scenarios` (diagnosis; non-comparable).
    mix: str = Field("tile", pattern="^(tile|custom)$")
    scenarios: list[str] = Field(default_factory=list)  # custom mix only
    mock_ms: float | None = Field(None, ge=100, le=60_000)
    mock_sigma: float | None = Field(None, ge=0, le=20_000)
    step_interval_s: float | None = Field(None, ge=3, le=120)
    step_users: int | None = Field(None, ge=1, le=8)
    agent_definitions: list[str] = Field(default_factory=list)  # e2e custom mix
    seed: int | None = Field(None, ge=0)             # reproducible corpus + report
    cache_mode: str = Field("warm", pattern="^(warm|cold)$")
    warmup_s: float | None = Field(None, ge=0, le=120)
    confirm_real: bool = False  # must be true for remote_real
    cloud_model: str | None = None
    cloud_api_key: str | None = None
    custom_base_url: str | None = None
    custom_model: str | None = None
    input_cost_per_mtok: float | None = Field(None, ge=0)
    output_cost_per_mtok: float | None = Field(None, ge=0)
    max_cost_usd: float | None = Field(None, gt=0, le=100_000)
    # Which metric to measure. "closed" runs the capability test (sessions
    # against a declared deadline); "open" runs the capacity test (offered
    # rate against queue divergence). The two are never combined.
    load_model: str | None = Field(None, pattern="^(closed|open)$")
    service_class: str | None = Field(None, pattern="^(interactive|batch)$")
    # Ladder rung override. "auto" (default) assigns by weigh-in; a named
    # rung skips the wait and is recorded as an operator override.
    service_rung: str | None = Field(None, pattern="^(auto|[a-z_]{1,32})$")
    # Re-measure the machine even when a fresh profile exists.
    force_weigh_in: bool = False
    # Aim the open-loop rate search at the measured machine (default on).
    arrival_calibrated: bool | None = None
    arrival_start_rate: float | None = Field(None, gt=0, le=10_000)
    arrival_step_factor: float | None = Field(None, gt=1.0, le=4.0)
    arrival_max_rate: float | None = Field(None, gt=0, le=100_000)
    arrival_hold_s: float | None = Field(None, ge=10, le=600)


def _resolve_dimensions(body: StartBody) -> tuple[str, str, str]:
    """Return (benchmark target, inference backend, internal runner mode)."""
    if body.benchmark_target is None:
        legacy = body.mode or "e2e"
        target = "agent_host" if legacy == "e2e" else "inference_engine"
        backend = "remote_mock" if legacy == "e2e" else legacy
    else:
        target = body.benchmark_target
        backend = body.inference_backend or (
            "local" if target == "integrated_node" else "remote_mock")

    allowed = {
        "agent_host": {"remote_mock", "remote_real"},
        "integrated_node": {"local"},
        "inference_engine": {"local", "remote_mock", "remote_real"},
    }
    if backend not in allowed[target]:
        raise HTTPException(
            400, f"{target} cannot use {backend}; valid backends: "
                 f"{', '.join(sorted(allowed[target]))}")
    mode = "e2e" if target in {"agent_host", "integrated_node"} else backend
    return target, backend, mode


@router.get("/scenarios")
async def get_scenarios() -> dict:
    from backend.capacity.scenarios import load_tile, load_e2e_workflows, load_e2e_tile
    return {"scenarios": scenario_list(), "tile": load_tile(),
            "e2e_workflows": [{"id": wid, **w} for wid, w in load_e2e_workflows().items()],
            "e2e_tile": load_e2e_tile(), "defaults": DEFAULTS}


@router.get("/engine")
async def get_engine() -> dict:
    return {**engine_mgr.status(), **(await engine_mgr.probe())}


@router.get("/models")
async def get_models() -> dict:
    return {"models": catalog_for_api(), "custom": {
        "id": "custom", "name": "Set up your own endpoint",
        "provider": "custom", "protocol": "OpenAI Chat Completions compatible",
    }}


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


async def _prepare(body: StartBody) -> dict:
    """Resolve everything a run needs, ONCE. A repeat set then builds N tests
    that differ only by seed — resolving per run would let a config change
    slip in between them."""
    target, inference_backend, mode = _resolve_dimensions(body)
    endpoint: dict | None = None
    if inference_backend == "remote_real":
        try:
            endpoint = resolve_endpoint(
                body.cloud_model, api_key=body.cloud_api_key,
                custom_base_url=body.custom_base_url, custom_model=body.custom_model,
                input_per_mtok=body.input_cost_per_mtok,
                output_per_mtok=body.output_cost_per_mtok,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not body.confirm_real:
            raise HTTPException(400, "remote inference spends real API credits — "
                                      "pass confirm_real=true")
        if body.max_cost_usd is None:
            raise HTTPException(400, "cloud runs require max_cost_usd as a dollar circuit breaker")
    if inference_backend == "local" and not (await engine_mgr.probe())["serving"]:
        raise HTTPException(409, "local engine is not serving — start it from the "
                                  "Capacity tab (or POST /capacity/engine/start)")
    schedule = scen_arrival_schedule()
    if body.service_rung not in (None, "auto"):
        from backend.capacity.scenarios import service_ladder
        if body.service_rung not in service_ladder():
            raise HTTPException(400, f"unknown service rung '{body.service_rung}' — "
                                      f"ladder: {sorted(service_ladder())}")
    cfg = {"load_model": (body.load_model or "closed"),
           "service_class": (body.service_class or "interactive"),
           "service_rung": (body.service_rung or "auto"),
           "force_weigh_in": bool(body.force_weigh_in),
           "arrival_calibrated": (True if body.arrival_calibrated is None
                                  else bool(body.arrival_calibrated)),
           "arrival_start_rate": body.arrival_start_rate or schedule["start_rate"],
           "arrival_step_factor": body.arrival_step_factor or schedule["step_factor"],
           "arrival_max_rate": body.arrival_max_rate or schedule["max_rate"],
           "arrival_hold_s": body.arrival_hold_s or schedule["hold_s"],
           "max_backlog": schedule["max_backlog"],
           "mock_ms": body.mock_ms, "mock_sigma": body.mock_sigma,
           "step_interval_s": body.step_interval_s,
           "step_users": body.step_users, "seed": body.seed,
           "cache_mode": body.cache_mode, "warmup_s": body.warmup_s,
           "max_cost_usd": body.max_cost_usd}
    e2e_router: dict | None = None
    if mode == "e2e":
        # Whole workflows are the unit: slower cadence and fewer samples are
        # needed to certify a rung. The workload ramp has no artificial cap.
        if target == "agent_host" and inference_backend == "remote_mock":
            default_interval = 10.0          # mock workflows finish in seconds
        elif target == "integrated_node":
            # Real workflows on local CPU inference run minutes each; a 300s
            # ceiling times out healthy runs and a 30s cadence outruns them.
            default_interval = 60.0
            cfg["e2e_timeout_s"] = 900.0
            # The steady-state window must contain whole workflows (minutes on
            # CPU), or the "steady" numbers sample the gaps between them.
            cfg["hold_s"] = 300.0
        else:
            default_interval = 30.0          # cloud-backed agent host
        cfg["step_interval_s"] = body.step_interval_s or default_interval
        cfg["min_samples"] = 2
        if target == "agent_host" and inference_backend == "remote_mock":
            mock_base = os.getenv(
                "CAPACITY_AGENT_HOST_MOCK_BASE_URL", "http://127.0.0.1:8901/v1")
            # Pre-flight like the local-engine path: real workflows against a
            # dead endpoint would 100%-error the run with nothing to learn.
            # A loopback mock is ours to run, so start the bundled one.
            from backend.capacity.mockrouter import ensure_mock_router
            try:
                await ensure_mock_router(mock_base)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            e2e_router = {
                "base_url": mock_base,
                "api_key": os.getenv("CAPACITY_AGENT_HOST_MOCK_API_KEY", "mock"),
                "model_label": "mock-tier-router",
            }
        elif target == "agent_host":
            e2e_router = {
                "base_url": endpoint["base_url"],
                "api_key": endpoint["api_key"],
                "model_override": endpoint["model"],
                "model_label": endpoint["name"],
                "provider": endpoint["provider"],
            }
        else:  # integrated_node: every role goes directly to local SGLang
            e2e_router = {
                "base_url": LOCAL_BASE,
                "api_key": os.getenv("CAPACITY_INTEGRATED_ROUTER_API_KEY",
                                      "unused"),
                "model_override": LOCAL_MODEL,
            }
    extra_workflows: dict = {}
    scenario_ids = list(body.scenarios)
    if mode == "e2e" and body.agent_definitions:
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
            scenario_ids.extend(extra_workflows)
    return {"mode": mode, "target": target, "inference_backend": inference_backend,
            "cfg": cfg, "scenario_ids": scenario_ids, "mix": body.mix,
            "extra_workflows": extra_workflows, "e2e_router": e2e_router,
            "endpoint": endpoint}


def _build_test(plan: dict, seed: int | None = None) -> CapacityTest:
    cfg = dict(plan["cfg"])
    if seed is not None:
        cfg["seed"] = int(seed)
    return CapacityTest(plan["mode"], list(plan["scenario_ids"]), cfg,
                        mix=plan["mix"],
                        extra_workflows=plan["extra_workflows"],
                        benchmark_target=plan["target"],
                        inference_backend=plan["inference_backend"],
                        e2e_router=plan["e2e_router"],
                        endpoint=plan["endpoint"])


def _require_idle() -> None:
    """One measurement at a time, single run or set."""
    if _current is not None and _current.status()["active"]:
        raise HTTPException(409, "a capacity test is already running")
    if _repeat is not None and _repeat.status()["active"]:
        raise HTTPException(409, "a repeat set is already running")


@router.post("/start")
async def start_test(body: StartBody,
                     _: None = Depends(_check_control_token)) -> dict:
    global _current, _task
    _require_idle()
    plan = await _prepare(body)
    _current = _build_test(plan)
    _task = asyncio.create_task(_run_and_keep(_current))
    logger.info("capacity test started: target=%s backend=%s scenarios=%s",
                plan["target"], plan["inference_backend"], _current.scenario_ids)
    return {"started": True, "mode": plan["mode"],
            "benchmark_target": plan["target"],
            "inference_backend": plan["inference_backend"], "mix": _current.mix,
            "scenarios": _current.scenario_ids}


async def _run_and_keep(test: CapacityTest):
    global _last_result
    await test.run()
    if test.result:
        _last_result = test.result


# ── repeat sets: three runs, a median, and a range ──────────────────────────

class RepeatBody(StartBody):
    runs: int = Field(3, ge=2, le=10)
    settle_s: float | None = Field(None, ge=0, le=1800)
    max_retries: int | None = Field(None, ge=0, le=5)


@router.post("/repeat/start")
async def start_repeat(body: RepeatBody,
                       _: None = Depends(_check_control_token)) -> dict:
    global _current, _repeat, _repeat_task
    _require_idle()
    plan = await _prepare(body)
    # The dollar circuit breaker is per run. A set of N runs would spend it N
    # times over, which is not what someone declaring a ceiling meant. Split it
    # so the SET honours the number the user typed.
    guard = plan["cfg"].get("max_cost_usd")
    per_run_guard = round(float(guard) / body.runs, 4) if guard else None
    if per_run_guard is not None:
        plan["cfg"]["max_cost_usd"] = per_run_guard

    def factory(seed: int, rung: str | None = None) -> CapacityTest:
        # The set owns _current so /status and /stop keep working per run.
        global _current
        if rung:
            plan["cfg"]["service_rung"] = rung     # set-pinned (recorded)
        _current = _build_test(plan, seed)
        return _current

    _repeat = RepeatSet(factory, runs=body.runs, seed=body.seed,
                        settle_s=(rpt.DEFAULT_SETTLE_S if body.settle_s is None
                                  else body.settle_s),
                        max_retries=(rpt.DEFAULT_MAX_RETRIES
                                     if body.max_retries is None
                                     else body.max_retries))
    _repeat_task = asyncio.create_task(_run_set(_repeat))
    logger.info("repeat set started: %d runs, base seed %d, target=%s",
                body.runs, _repeat.seed, plan["target"])
    return {"started": True, "runs": body.runs, "base_seed": _repeat.seed,
            "mode": plan["mode"], "benchmark_target": plan["target"],
            "inference_backend": plan["inference_backend"], "mix": plan["mix"],
            "max_cost_usd_total": guard, "max_cost_usd_per_run": per_run_guard}


async def _run_set(rs: RepeatSet):
    global _last_repeat
    await rs.run()
    _last_repeat = rs.result


@router.get("/repeat/status")
async def repeat_status() -> dict:
    if _repeat is None:
        return {"active": False, "phase": "idle", "result": _last_repeat}
    return _repeat.status()


@router.post("/stop")
async def stop_test() -> dict:
    stopping = False
    # Stop the SET first: stopping only the child would let the next run start.
    if _repeat is not None and _repeat.status()["active"]:
        _repeat.stop()
        stopping = True
    if _current is not None and _current.status()["active"]:
        _current.stop()
        stopping = True
    if not stopping:
        raise HTTPException(409, "no capacity test is running")
    return {"stopping": True}


@router.get("/status")
async def get_status() -> dict:
    base = (_current.status() if _current is not None
            else {"active": False, "phase": "idle", "result": _last_result})
    if _repeat is not None:
        rs = _repeat.status()
        base = {**base, "repeat": {k: v for k, v in rs.items() if k != "current"}}
        if rs["active"]:
            # Between runs there is no child, but the set is still working.
            base["active"] = True
            if _repeat.current is None:
                base["phase"] = rs["phase"]
    return base


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
