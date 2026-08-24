"""
FastAPI application — WebSocket hub + REST endpoints.

Endpoints:
  POST /run          — Start a new swarm run (and concurrent A/B single-model run)
  GET  /run/{run_id} — Fetch final RunResult
  WS   /ws/{run_id}  — Stream SwarmEvents in real time
  GET  /agents       — List available agents (A2A Agent Card discovery)
  GET  /.well-known/agent.json — A2A discovery for this host
  GET  /health       — Liveness check
  GET  /metrics      — Prometheus metrics

WebSocket event flow:
  run_started
  single_started                    ← A/B panel starts immediately
  single_token × N                  ← streaming tokens from large model
  graph_ready                       ← task graph rendered in UI
  task_started × M (parallel)
  task_completed × M (as they land)
  synthesis_started
  run_completed                     ← swarm panel shows final answer
  single_completed                  ← A/B panel shows final answer + timing
"""
import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from pathlib import Path

# Configure root logging early so every module's logger propagates to stdout
# (otherwise uvicorn/docker only shows access logs — application errors and
# tracebacks get swallowed).  Level controlled via LOG_LEVEL env var (INFO default).
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)
# Quiet down the noisy ones so real signal stays visible
for noisy in ("httpx", "httpcore", "openai._base_client"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from backend.schemas.models import (
    RunRequest,
    RunResult,
    SwarmEvent,
    EventType,
)
from backend.protocols.a2a_cards import all_agent_cards, ORCHESTRATOR_CARD
from backend.repositories import persistence as db
from backend.db.base import dispose_engine, get_session, create_schema
from backend.observability.metrics import (
    runs_total,
    run_latency_seconds,
    active_runs,
    tasks_total,
    task_latency_seconds,
    websocket_connections,
)

import time


# ── In-memory cache of live run results (durable copy is in SQLite) ──────────
_run_results: dict[str, RunResult] = {}

# Registry of live asyncio Tasks indexed by (run_id, task_id).
# Live run tasks, keyed by run_id, so /run/{id}/kill can cancel a run.
_run_tasks: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the SQLite schema if it doesn't exist (no migration tooling).
    # Best-effort: a DB problem shouldn't crash the API — the persistence facade
    # degrades gracefully and the in-memory run cache still works.
    try:
        await create_schema()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "DB schema creation on startup failed (continuing): %s", exc
        )
    # Start the job scheduler (scans due jobs and fires runs). Opt-out via
    # SCHEDULER_ENABLED=0 for environments that don't want background firing.
    scheduler_started = False
    if os.getenv("SCHEDULER_ENABLED", "1").lower() in ("1", "true", "yes"):
        try:
            from backend.scheduling.scheduler import start_scheduler
            start_scheduler()
            scheduler_started = True
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Scheduler failed to start (continuing): %s", exc
            )
    # Multi-process orchestrator (opt-in via ADL_WORKERS=N): the control
    # process spawns N run-executor processes and dispatches runs to them —
    # the GIL caps a single asyncio process at ~one core (measured: agent-host
    # capacity certified 6-9 workflows with the host at 2% CPU).
    from backend import workerpool as wp
    if wp.pool_enabled():
        try:
            await wp.start_pool()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "executor pool failed to start (running in-process): %s", exc)
    yield
    if wp.pool_enabled():
        await wp.stop_pool()
    if scheduler_started:
        from backend.scheduling.scheduler import shutdown_scheduler
        shutdown_scheduler()
    await dispose_engine()


import logging
logger = logging.getLogger(__name__)

app = FastAPI(title="Xeon Agent Swarm Demo", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log the full Pydantic validation detail so 422s are diagnosable in docker compose logs."""
    logger.warning(
        "422 validation error — %s %s body_errors=%s",
        request.method, request.url.path, exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# CORS is OFF by default, and that is the correct production posture: the tunnel
# serves the UI, REST and WebSocket from ONE origin, so the browser never makes a
# cross-origin request and needs no CORS headers at all.
#
# It matters that this is not permissive by default. `allow_origins=["*"]` together
# with `allow_credentials=True` makes Starlette echo back whichever Origin asked,
# which would let ANY website make credentialed requests to a backend that executes
# code and fires real tools (email, SMS, SQL writes).
#
# Opt in only for a genuinely cross-origin dev workflow — e.g. a hosted editor on
# public HTTPS calling this box on a private Tailscale IP:
#   DEV_CORS_ORIGINS=https://editor.example        # explicit origin (preferred)
#   DEV_CORS_ORIGINS=https://a.example,https://b   # several
#   DEV_CORS_ORIGINS=*                             # any origin — dev boxes ONLY
_dev_cors = os.getenv("DEV_CORS_ORIGINS", "").strip()
if _dev_cors:
    _cors_origins = [o.strip() for o in _dev_cors.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Starlette 0.27+ native Private Network Access. Required for a public-HTTPS
        # origin to preflight a private-IP target; without it CORSMiddleware returns
        # HTTP 400 "Disallowed CORS private-network". Dev-only, so it lives here.
        allow_private_network=True,
    )
    logger.warning(
        "DEV CORS enabled for origins=%s (private-network preflight allowed) — "
        "never set DEV_CORS_ORIGINS on a public deployment", _cors_origins,
    )


# ── Routers (jobs / runs / connectors — durable orchestration API) ───────────
from backend.routers.jobs import router as jobs_router
from backend.routers.runs import router as runs_router
from backend.routers.connectors import router as connectors_router
from backend.routers.tools import router as tools_router
from backend.routers.toolbox import router as toolbox_router
from backend.routers.capacity import router as capacity_router
from backend.routers.agent_definitions import router as agent_defs_router

app.include_router(jobs_router)
app.include_router(runs_router)
app.include_router(connectors_router)
app.include_router(tools_router)
app.include_router(toolbox_router)
app.include_router(capacity_router)
app.include_router(agent_defs_router)


# ── WebSocket connection manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, ws: WebSocket):
        await ws.accept()
        self.connections.setdefault(run_id, []).append(ws)
        websocket_connections.inc()

    def disconnect(self, run_id: str, ws: WebSocket):
        conns = self.connections.get(run_id, [])
        if ws in conns:
            conns.remove(ws)
        websocket_connections.dec()

    async def broadcast(self, run_id: str, event: SwarmEvent):
        # Wire format is a CloudEvents 1.0 structured-mode envelope.
        # See docs/standards.md §2.2 and frontend fromCloudEvent().
        import json as _json
        envelope = _json.dumps(event.to_cloudevent())
        for ws in list(self.connections.get(run_id, [])):
            try:
                await ws.send_text(envelope)
            except Exception:
                pass


manager = ConnectionManager()


async def broadcast_event(run_id: str, event: SwarmEvent):
    """Broadcast to local WS clients and, on an executor process, relay the
    event to the control process (where the browsers are connected)."""
    await manager.broadcast(run_id, event)
    from backend import workerpool as wp
    if wp.is_worker():
        await wp.forward_event(run_id, event.model_dump(mode="json"))


# Pending HITL plan-approval decisions, keyed by run_id. POST /run/{id}/approve
# resolves the future the run is awaiting (see run_deepagents / run_with_adapter).
_pending_approvals: dict[str, asyncio.Future] = {}
# Decisions delivered before the run registered its awaiter. run_with_adapter emits
# the `awaiting_approval` WS event just *before* _await_approval registers the future,
# so a client that reacts immediately would otherwise 404 and hang the run. We stash
# the decision here and _await_approval consumes it as soon as it arms.
_early_decisions: dict[str, str] = {}


async def _await_approval(run_id: str):
    """Block until POST /run/{run_id}/approve delivers a decision string.

    Order-independent: if a decision already arrived (client raced ahead of the
    awaiter), consume it immediately instead of waiting on a future no one will
    resolve. A fresh future is created per call, so multiple interrupts re-arm."""
    early = _early_decisions.pop(run_id, None)
    if early is not None:
        return early
    fut = asyncio.get_event_loop().create_future()
    _pending_approvals[run_id] = fut
    try:
        return await fut
    finally:
        _pending_approvals.pop(run_id, None)


async def run_deepagents(
    run_id: str,
    query: str,
    validator_enabled: bool = True,
    *,
    job_id: str | None = None,
    trigger: str = "manual",
    plan_approval: bool | None = None,
    enabled_tools: list[str] | None = None,
    budget: dict | None = None,
    router_base_url: str | None = None,
    router_api_key: str | None = None,
    router_model: str | None = None,
    router_provider: str = "openai",
    toolless: bool = False,
):
    """deepagents (ADL) engine: a single deep agent decomposes + delegates + synthesizes,
    streamed through the event adapter onto the same WS + DB surfaces as the old swarm.

    The single ADL run engine (the old swarm engine was removed at cutover). The adapter
    owns step/attempt/validation rows, the routing rollup, and run finalize; this wrapper
    owns run creation, the checkpointer lifecycle, metrics, and the Langfuse trace.
    When validator_enabled, L1/L2 judging + bounded retry are wired in (Stage 3).
    """
    t0 = time.perf_counter()
    active_runs.inc()
    runs_total.inc()

    await db.create_run(
        run_id, query, job_id=job_id, trigger=trigger,
        config={"engine": "deepagents", "validator_enabled": validator_enabled,
                "enabled_tools": enabled_tools or [],
                "router_override": bool(router_base_url)},
    )
    if job_id:
        await db.set_job_last_run(job_id, run_id)

    from backend.observability import langfuse_client as lf
    trace_id = lf.start_run_trace(run_id, query, {"engine": "deepagents"})
    await db.set_run_status(run_id, "running", langfuse_trace_id=trace_id)

    try:
        from backend.agents.core import build_agent
        from backend.inference.model import ModelFactory
        from backend.observability.event_adapter import run_with_adapter, _budget_from_env
        from backend.observability.validation_judge import (
            make_judge, make_redispatch, make_synthesis_grader, make_partial_synthesizer)
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        mf = ModelFactory(base_url=router_base_url, api_key=router_api_key,
                          model_override=router_model, provider=router_provider)
        judge = make_judge(mf) if validator_enabled else None
        redispatch = make_redispatch(mf) if validator_enabled else None
        synthesis_grader = make_synthesis_grader(mf) if validator_enabled else None
        # Always available: only fires when a budget stop abandons the graph before the
        # main agent synthesized, so partial results still yield a final answer.
        partial_synthesizer = make_partial_synthesizer(mf)
        checkpoint_base = (os.environ.get("ADL_CHECKPOINT_DB")
                           or os.environ.get("CHECKPOINT_DB", "./data/adl_checkpoints.db"))
        # One checkpoint DB PER RUN: a single shared file serializes every
        # concurrent workflow on one SQLite writer (measured: agent-host
        # capacity certified at 9 sessions with the CPU at 2% — the writer
        # lock, not the hardware, was the ceiling). The file is deleted after
        # a clean finish; it only ever held live/resume state the UI never
        # reads. On a crash it is left behind for postmortem.
        ckpt_dir = os.path.join(os.path.dirname(checkpoint_base) or ".", "run_checkpoints")
        os.makedirs(ckpt_dir, exist_ok=True)
        checkpoint_db = os.path.join(ckpt_dir, f"{run_id}.db")

        # HITL plan approval: per-run flag wins; None falls back to the ADL_PLAN_APPROVAL
        # env default for MANUAL runs only — a scheduled run must never pause (it would
        # hang unattended). When on, the graph pauses after planning and run_with_adapter
        # awaits this coroutine for a decision (POST /run/{run_id}/approve).
        if plan_approval is None:
            env_flag = os.environ.get("ADL_PLAN_APPROVAL", "").strip().lower() in ("1", "true", "yes")
            plan_approval = env_flag and trigger == "manual"
        approval = (lambda: _await_approval(run_id)) if plan_approval else None

        # The adapter handles run_started, steps, validation, finalize (incl. the
        # routing + validation rollup), budgets, and run_completed/run_metrics over WS.
        async with AsyncSqliteSaver.from_conn_string(checkpoint_db) as checkpointer:
            # WAL within the run's own file: workers checkpoint concurrently.
            await checkpointer.conn.execute("PRAGMA journal_mode=WAL")
            # toolless: self-contained benchmark workflows strip ALL worker tool
            # grants (empty tools_by_name) AND deepagents' builtin scratchpad
            # tools from workers, so no role can start a tool loop.
            agent = build_agent(checkpointer, plan_approval=plan_approval,
                                enabled_tools=enabled_tools, model_factory=mf,
                                tools_by_name={} if toolless else None,
                                toolless=toolless)
            summary = await run_with_adapter(
                agent, query, run_id,
                broadcast=broadcast_event,
                judge=judge, redispatch=redispatch,
                synthesis_grader=synthesis_grader,
                partial_synthesizer=partial_synthesizer, approval=approval,
                # Definition budgets override env defaults key-by-key; a partial
                # budget must not silently unlimit the other dimensions.
                budget=({**_budget_from_env(),
                         **{k: int(v) for k, v in budget.items()
                            if k in ("max_subagents", "max_tool_hops",
                                     "max_total_tokens") and v is not None}}
                        if budget else None),
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        run_latency_seconds.observe(latency_ms / 1000)
        lf.complete_run_trace(
            run_id, output=summary.get("final_answer") or "",
            metrics=summary.get("routing", {}), status="completed",
        )
        # Clean finish (completed OR failed-and-recorded): the per-run
        # checkpoint file held only live/resume state — remove it and its
        # WAL/SHM siblings. A crashed process skips this and leaves evidence.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(checkpoint_db + suffix)
            except OSError:
                pass
    except Exception as exc:  # checkpointer/agent-build failures (run errors are
        # caught inside run_with_adapter and reported as a failed run there).
        logger.exception("deepagents run %s failed to start", run_id)
        await db.set_run_status(run_id, "failed", error=str(exc))
        await broadcast_event(run_id, SwarmEvent(
            event=EventType.error, run_id=run_id, payload={"error": str(exc)}))
    finally:
        active_runs.dec()


def launch_run(
    query: str,
    *,
    validator_enabled: bool = True,
    job_id: str | None = None,
    trigger: str = "manual",
    plan_approval: bool | None = None,
    enabled_tools: list[str] | None = None,
    budget: dict | None = None,
    router_base_url: str | None = None,
    router_api_key: str | None = None,
    router_model: str | None = None,
    router_provider: str = "openai",
    toolless: bool = False,
) -> str:
    """Create a run_id and kick off the ADL deepagents engine in the background.

    Shared entry point for ad-hoc /run, scheduled fires, and /jobs/{id}/run-now, so
    they all go through identical persistence paths. The old swarm engine was removed
    at cutover; ADL_ENGINE is retained only to warn if a stale `swarm` value is set.
    """
    run_id = str(uuid.uuid4())
    engine = os.environ.get("ADL_ENGINE", "deepagents").strip().lower()
    if engine != "deepagents":
        logger.warning("ADL_ENGINE=%r is no longer supported (swarm engine removed at "
                       "cutover); running the deepagents engine.", engine)

    # Multi-process dispatch: on a control process with an executor pool the
    # run executes in a child process (GIL relief); the shared WAL SQLite is
    # the record and the executor relays WS events back here. Pool empty/dead
    # -> fall through to in-process execution rather than refuse the run.
    from backend import workerpool as wp
    if wp.pool_enabled():
        url = wp.next_worker()
        if url is not None:
            wp.assign(run_id, url)
            payload = {
                "run_id": run_id, "query": query,
                "validator_enabled": validator_enabled, "job_id": job_id,
                "trigger": trigger, "plan_approval": plan_approval,
                "enabled_tools": enabled_tools, "budget": budget,
                "router_base_url": router_base_url,
                "router_api_key": router_api_key,
                "router_model": router_model,
                "router_provider": router_provider,
                "toolless": toolless,
            }

            async def _dispatch():
                try:
                    await wp.dispatch_run(url, payload)
                except Exception as exc:
                    logger.exception("dispatch of run %s to %s failed", run_id, url)
                    # The executor never created the Run row — create the
                    # failure record here so the run is visible, not vanished.
                    try:
                        await db.create_run(run_id, query, job_id=job_id,
                                            trigger=trigger,
                                            config={"engine": "deepagents"})
                        await db.set_run_status(run_id, "failed",
                                                error=f"dispatch failed: {exc}")
                    except Exception:
                        pass

            asyncio.create_task(_dispatch())
            return run_id

    task = asyncio.create_task(run_deepagents(
        run_id, query,
        validator_enabled=validator_enabled,
        job_id=job_id,
        trigger=trigger,
        plan_approval=plan_approval,
        enabled_tools=enabled_tools,
        budget=budget,
        router_base_url=router_base_url,
        router_api_key=router_api_key,
        router_model=router_model,
        router_provider=router_provider,
        toolless=toolless,
    ))
    _run_tasks[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: _run_tasks.pop(rid, None))
    return run_id


# ── DB serializers ────────────────────────────────────────────────────────────

def _run_to_dict(run) -> dict:
    """Serialize a Run ORM object (with eager-loaded steps + attempts) for the API."""
    return {
        "run_id": run.id,
        "job_id": run.job_id,
        "trigger": run.trigger,
        "query": run.query,
        "config": run.config,
        "status": run.status,
        "task_graph": run.task_graph,
        "document": run.document_result,
        "metrics": run.metrics,
        "langfuse_trace_id": run.langfuse_trace_id,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "steps": [
            {
                "step_key": s.step_key,
                "type": s.type,
                "objective": s.objective,
                "deliverable_format": s.deliverable_format,
                "dependencies": s.dependencies,
                "status": s.status,
                "result": s.result,
                "confidence": s.confidence,
                "total_attempts": s.total_attempts,
                "latency_ms": s.latency_ms,
                "attempts": [
                    {
                        "attempt_no": a.attempt_no,
                        "status": a.status,
                        "model_id": a.model_id,
                        "correction_hint": a.correction_hint,
                        "latency_ms": a.latency_ms,
                        # routing telemetry: what the router decided for this call
                        "tier_requested": a.tier_requested,
                        "tier_observed": a.tier_observed,
                        "category": a.category,
                        "cache_hit": a.cache_hit,
                        "tokens_in": a.tokens_in,
                        "tokens_out": a.tokens_out,
                    }
                    for a in sorted(s.attempts, key=lambda x: x.attempt_no)
                ],
                "validations": [
                    {
                        "level": v.level,
                        "verdict": v.verdict,
                        "score": v.score,
                        "validator_tier": v.validator_tier,
                        "rubric_id": v.rubric_id,
                        "retries_used": v.retries_used,
                        "escalated": v.escalated,
                        "detail": v.detail,
                    }
                    for v in sorted(s.validations, key=lambda x: x.created_at)
                ],
            }
            for s in sorted(run.steps, key=lambda x: x.step_key)
        ],
    }


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.post("/run")
async def start_run(request: RunRequest):
    """Start a new ad-hoc swarm run."""
    run_id = launch_run(
        request.query,
        validator_enabled=request.validator_enabled,
        trigger="manual",
        plan_approval=request.plan_approval,
        enabled_tools=request.enabled_tools,
    )
    return {"run_id": run_id}


@app.get("/run/{run_id}")
async def get_run(run_id: str):
    """Fetch the final RunResult (may be incomplete if still running)."""
    result = _run_results.get(run_id)
    if result:
        return result
    # Fall back to the durable DB record (survives restarts).
    from backend.repositories import runs as runs_repo
    from backend.db.base import get_sessionmaker
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            run = await runs_repo.get_run(session, run_id)
            if run:
                return _run_to_dict(run)
    except Exception as exc:
        logger.warning("DB lookup for run %s failed: %s", run_id, exc)
    return {"run_id": run_id, "status": "not_found"}


# ── Multi-process internal surface ────────────────────────────────────────────
# Executor side: accept a dispatched run. Control side: accept relayed events.
# Both are token-guarded — the control process is publicly reachable through
# the tunnel, and executors only ever bind 127.0.0.1.

@app.post("/internal/run")
async def internal_run(request: Request):
    from backend import workerpool as wp
    if not wp.check_token(request.headers.get("X-Internal-Token")):
        raise HTTPException(403, "internal endpoint")
    body = await request.json()
    run_id = body.pop("run_id")
    task = asyncio.create_task(run_deepagents(run_id, body.pop("query"), **body))
    _run_tasks[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: _run_tasks.pop(rid, None))
    return {"accepted": run_id}


@app.post("/internal/events")
async def internal_events(request: Request):
    from backend import workerpool as wp
    if not wp.check_token(request.headers.get("X-Internal-Token")):
        raise HTTPException(403, "internal endpoint")
    body = await request.json()
    await manager.broadcast(body["run_id"], SwarmEvent(**body["event"]))
    return {"relayed": True}


@app.post("/run/{run_id}/kill")
async def kill_run(run_id: str):
    """Cancel a running deepagents run. The run's asyncio.Task is cancelled (the
    deepagents graph stream is abandoned) and the run is marked aborted."""
    from backend import workerpool as wp
    if wp.pool_enabled() and wp.owner(run_id):
        return await wp.proxy_post(wp.owner(run_id), f"/run/{run_id}/kill")
    task = _run_tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        await db.set_run_status(run_id, "aborted", error="killed by user")
        return {"status": "killed", "run_id": run_id}
    return {"status": "not_found", "run_id": run_id}


@app.post("/run/{run_id}/approve")
async def approve_run(run_id: str, body: dict):
    """HITL plan-approval gate (deepagents engine). Deliver a decision to a run that
    paused after planning: {"decision": "approve"} resumes, {"decision": "reject"}
    aborts.

    Delivery is order-independent: the `awaiting_approval` WS event is emitted just
    before the run registers its awaiter, so a responsive client can arrive first.
    When the future is present we resolve it; otherwise we stash the decision for the
    awaiter to pick up when it arms (see _await_approval), avoiding a 404-then-hang."""
    from backend import workerpool as wp
    if wp.pool_enabled() and wp.owner(run_id):
        # The awaiting future lives in the executor running the graph.
        return await wp.proxy_post(wp.owner(run_id), f"/run/{run_id}/approve", body)
    decision = (body or {}).get("decision", "approve")
    fut = _pending_approvals.get(run_id)
    if fut is not None and not fut.done():
        fut.set_result(decision)
        return {"run_id": run_id, "decision": decision, "delivery": "resolved"}
    _early_decisions[run_id] = decision
    return {"run_id": run_id, "decision": decision, "delivery": "queued"}



@app.websocket("/ws/{run_id}")
async def websocket_endpoint(run_id: str, ws: WebSocket):
    await manager.connect(run_id, ws)
    try:
        while True:
            await ws.receive_text()  # keep alive; client sends pings
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(run_id, ws)


@app.get("/agents")
async def list_agents():
    """A2A Agent Card discovery — lists all available agents."""
    return {"agents": all_agent_cards()}


@app.get("/.well-known/agent.json")
async def well_known_agent():
    """Standard A2A discovery endpoint for this host."""
    return ORCHESTRATOR_CARD


@app.get("/health")
async def health():
    return {"status": "ok", "service": "xeon-agent-swarm"}


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve TTS audio files generated for run executive summaries."""
    audio_dir = Path(os.getenv("AUDIO_DIR", "/data/audio"))
    path = audio_dir / filename
    if not path.exists() or not filename.endswith(".mp3"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Single-origin SPA serving ─────────────────────────────────────────────────
# When the frontend has been built (frontend/dist), serve it from THIS app so the
# UI, REST, and WebSocket all share one origin. That is what makes hosting simple:
# a Cloudflare Tunnel / subdomain needs no rebuild, there is no CORS, and the page's
# scheme decides ws:// vs wss:// automatically (see frontend/src/lib/origin.ts).
#
# Registered last so every API route above takes precedence; API prefixes are
# explicitly excluded from the catch-all so an unknown API path still 404s as JSON
# instead of silently returning index.html.
_DIST = Path(os.getenv("FRONTEND_DIST", "frontend/dist"))

# HAND-MAINTAINED: every top-level API path prefix must be listed here, or an
# unmatched path under it silently returns index.html with a 200 instead of a 404.
# Add an entry whenever a new top-level route or router prefix is registered.
_API_PREFIXES = (
    "run", "runs", "jobs", "connectors", "toolbox", "tools", "ws",
    "health", "metrics", "docs", "redoc", "openapi.json", "audio",
    "agents", ".well-known", "capacity", "agent-definitions", "internal",
)

if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    _assets = _DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """Serve a built static file when it exists, else index.html (SPA routing)."""
        first = full_path.split("/", 1)[0]
        if first in _API_PREFIXES:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")

    logger.info("Serving built SPA from %s (single-origin mode)", _DIST)
else:
    logger.info("No built frontend at %s — API-only mode (run `npm run build`)", _DIST)
