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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
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
    yield
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,  # Starlette 0.27+ native PNA support — required for
                                 # Lovable (public HTTPS) → Tailscale (private IP) requests.
                                 # Without this, CORSMiddleware returns HTTP 400
                                 # "Disallowed CORS private-network" for any preflight
                                 # that includes Access-Control-Request-Private-Network.
)


# ── Routers (jobs / runs / connectors — durable orchestration API) ───────────
from backend.routers.jobs import router as jobs_router
from backend.routers.runs import router as runs_router
from backend.routers.connectors import router as connectors_router
from backend.routers.toolbox import router as toolbox_router

app.include_router(jobs_router)
app.include_router(runs_router)
app.include_router(connectors_router)
app.include_router(toolbox_router)


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
):
    """deepagents (ADL) engine: a single deep agent decomposes + delegates + synthesizes,
    streamed through the event adapter onto the same WS + DB surfaces as the old swarm.

    The single ADL run engine (the old swarm engine was removed at cutover). The adapter
    owns step/attempt/validation rows, the cost rollup, and run finalize; this wrapper
    owns run creation, the checkpointer lifecycle, metrics, and the Langfuse trace.
    When validator_enabled, L1/L2 judging + bounded retry are wired in (Stage 3).
    """
    t0 = time.perf_counter()
    active_runs.inc()
    runs_total.inc()

    await db.create_run(
        run_id, query, job_id=job_id, trigger=trigger,
        config={"engine": "deepagents", "validator_enabled": validator_enabled},
    )
    if job_id:
        await db.set_job_last_run(job_id, run_id)

    from backend.observability import langfuse_client as lf
    trace_id = lf.start_run_trace(run_id, query, {"engine": "deepagents"})
    await db.set_run_status(run_id, "running", langfuse_trace_id=trace_id)

    try:
        from backend.agents.core import build_agent
        from backend.inference.model import ModelFactory
        from backend.observability.event_adapter import run_with_adapter
        from backend.observability.validation_judge import (
            make_judge, make_redispatch, make_synthesis_grader)
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        mf = ModelFactory()
        judge = make_judge(mf) if validator_enabled else None
        redispatch = make_redispatch(mf) if validator_enabled else None
        synthesis_grader = make_synthesis_grader(mf) if validator_enabled else None
        checkpoint_db = (os.environ.get("ADL_CHECKPOINT_DB")
                         or os.environ.get("CHECKPOINT_DB", "./data/adl_checkpoints.db"))

        # HITL plan approval (opt-in): when ADL_PLAN_APPROVAL is on, the graph pauses
        # after planning and run_with_adapter awaits this coroutine for a decision,
        # delivered by POST /run/{run_id}/approve. Off → auto-approve, never blocks.
        plan_approval = os.environ.get("ADL_PLAN_APPROVAL", "").strip().lower() in ("1", "true", "yes")
        approval = (lambda: _await_approval(run_id)) if plan_approval else None

        # The adapter handles run_started, steps, validation, finalize (incl. the
        # cost + validation rollup), budgets, and run_completed/run_metrics over WS.
        async with AsyncSqliteSaver.from_conn_string(checkpoint_db) as checkpointer:
            agent = build_agent(checkpointer)
            summary = await run_with_adapter(
                agent, query, run_id,
                broadcast=manager.broadcast,
                judge=judge, redispatch=redispatch,
                synthesis_grader=synthesis_grader, approval=approval,
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        run_latency_seconds.observe(latency_ms / 1000)
        lf.complete_run_trace(
            run_id, output=summary.get("final_answer") or "",
            metrics=summary.get("cost", {}), status="completed",
        )
    except Exception as exc:  # checkpointer/agent-build failures (run errors are
        # caught inside run_with_adapter and reported as a failed run there).
        logger.exception("deepagents run %s failed to start", run_id)
        await db.set_run_status(run_id, "failed", error=str(exc))
        await manager.broadcast(run_id, SwarmEvent(
            event=EventType.error, run_id=run_id, payload={"error": str(exc)}))
    finally:
        active_runs.dec()


def launch_run(
    query: str,
    *,
    validator_enabled: bool = True,
    job_id: str | None = None,
    trigger: str = "manual",
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
    task = asyncio.create_task(run_deepagents(
        run_id, query,
        validator_enabled=validator_enabled,
        job_id=job_id,
        trigger=trigger,
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
                    }
                    for a in sorted(s.attempts, key=lambda x: x.attempt_no)
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


@app.post("/run/{run_id}/kill")
async def kill_run(run_id: str):
    """Cancel a running deepagents run. The run's asyncio.Task is cancelled (the
    deepagents graph stream is abandoned) and the run is marked aborted."""
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
