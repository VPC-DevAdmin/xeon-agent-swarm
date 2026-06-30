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
    AgentResult,
    ArtifactType,
    KillTaskRequest,
    RunRequest,
    RunResult,
    SwarmEvent,
    SwarmState,
    TaskSpec,
    TaskStatus,
    TaskType,
    EventType,
)
from backend.agents.orchestrator import orchestrate_with_events
from backend.agents.planner import plan_phase, PlanningFailed
from backend.agents.worker import execute_task_with_validation
from backend.agents.reducer import synthesize
from backend.graph.swarm_graph import validate_task_graph
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
# Populated by run_swarm() so the /kill endpoint can cancel them.
_running_tasks: dict[str, dict[str, asyncio.Task]] = {}


def _build_writing_context(task: TaskSpec, results: dict) -> dict[str, str]:
    """
    Build an enriched context dict for the writing worker.

    Plain workers only get `result` (a brief string). The writing task needs the
    full substance from each specialist: table rows, citation snippets, extracted
    data points — otherwise it can only produce thin generalisations.

    Each dependency's context value is a concatenation of:
      1. The `result` summary string
      2. Table rows (if a table artifact was produced)
      3. Citation snippets (if a citation_set was produced)
      4. Extracted data points (if an extracted_data artifact was produced)
      5. Chart series values (if a chart artifact was produced)
    """
    context: dict[str, str] = {}
    for dep in task.dependencies:
        if dep not in results:
            continue
        agent_result = results[dep]
        parts: list[str] = [agent_result.result or ""]

        for art in agent_result.artifacts:
            c = art.content or {}

            if art.type == ArtifactType.table:
                headers = c.get("headers", [])
                rows = c.get("rows", [])
                caption = c.get("caption", "")
                table_lines = [f"\n[Table: {caption}]"]
                if headers:
                    table_lines.append(" | ".join(str(h) for h in headers))
                    table_lines.append("-" * max(20, len(" | ".join(headers))))
                for row in rows[:15]:  # cap to avoid token overflow
                    table_lines.append(" | ".join(str(cell) for cell in row))
                parts.append("\n".join(table_lines))

            elif art.type == ArtifactType.citation_set:
                citations = c.get("citations", [])
                cite_lines = ["\n[Sources]"]
                for cit in citations[:6]:
                    snippet = cit.get("snippet", "")
                    title = cit.get("title", "")
                    url = cit.get("url", "")
                    cite_lines.append(f"- {title}: {snippet} <{url}>")
                parts.append("\n".join(cite_lines))

            elif art.type == ArtifactType.extracted_data:
                pts = c.get("data_points", [])
                desc = c.get("description", "")
                data_lines = [f"\n[Extracted Data: {desc}]"]
                for pt in pts:
                    label = pt.get("label", "")
                    value = pt.get("value", "")
                    unit = pt.get("unit", "")
                    data_lines.append(f"- {label}: {value}{' ' + unit if unit else ''}")
                parts.append("\n".join(data_lines))

            elif art.type == ArtifactType.chart:
                series = c.get("series", [])
                caption = c.get("caption", "")
                chart_lines = [f"\n[Chart data: {caption}]"]
                for s in series[:3]:
                    pts_str = ", ".join(
                        f"{p['x']}={p['y']}" for p in s.get("data", [])[:8]
                    )
                    chart_lines.append(f"  {s.get('name', '')}: {pts_str}")
                parts.append("\n".join(chart_lines))

        # Cap total context per dependency to avoid prompt overflow.
        # Budget: with TEXT_MAX_MODEL_LEN=16384, reserve ~4k for system prompt
        # + task contract + writing output buffer, leaving ~12k tokens (~48k
        # chars) for dependency context.  At 1200 chars × 10 deps = 12k chars
        # we're well under budget even with long task graphs.
        _PER_DEP_CAP = 1200
        combined = "\n\n".join(p for p in parts if p.strip())
        if len(combined) > _PER_DEP_CAP:
            combined = combined[:_PER_DEP_CAP] + "\n…[truncated]"
        context[dep] = combined
    return context


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


# ── Swarm pipeline ───────────────────────────────────────────────────────────

# Decompose-and-verify (spec v3): best-of-N planning with verifier selection.
# Default ON; set BEST_OF_N_PLANNING=0 to fall back to single-shot decomposition.
_BEST_OF_N = os.getenv("BEST_OF_N_PLANNING", "1").lower() not in ("0", "false", "no")


async def _decompose(query: str, run_id: str, broadcast, critique: str | None):
    """Produce a TaskGraph: best-of-N decompose-and-verify when enabled, else the
    single-shot orchestrator. Falls back to single-shot if planning fails."""
    if _BEST_OF_N:
        try:
            return await plan_phase(query, run_id, broadcast, feedback=critique)
        except PlanningFailed:
            logger.warning("best-of-N planning failed — falling back to single-shot")
    return await orchestrate_with_events(query, run_id, broadcast, critique=critique)


async def run_swarm(
    run_id: str,
    query: str,
    validator_enabled: bool = True,
    *,
    job_id: str | None = None,
    trigger: str = "manual",
):
    """Full swarm pipeline: orchestrate → validate graph → parallel workers → reduce."""
    t0 = time.perf_counter()
    active_runs.inc()
    runs_total.inc()

    state = SwarmState(run_id=run_id, query=query, validator_enabled=validator_enabled)

    # Durable record — created before any work so the run is visible in the API
    # immediately (status transitions through orchestrating → running → done).
    await db.create_run(
        run_id, query, job_id=job_id, trigger=trigger,
        config={"validator_enabled": validator_enabled},
    )
    if job_id:
        await db.set_job_last_run(job_id, run_id)

    # Optional Langfuse trace (no-op when unconfigured).
    from backend.observability import langfuse_client as lf
    trace_id = lf.start_run_trace(run_id, query, {"validator_enabled": validator_enabled})
    await db.set_run_status(run_id, "orchestrating", langfuse_trace_id=trace_id)

    await manager.broadcast(
        run_id,
        SwarmEvent(
            event=EventType.run_started,
            run_id=run_id,
            payload={"query": query},
        ),
    )

    try:
        # ── Step 1: Orchestrate (with graph validation + retry) ───────────────
        orchestrator_retries = 0
        task_graph = None
        critique = None

        while task_graph is None and orchestrator_retries < 2:
            task_graph = await _decompose(
                query, run_id, manager.broadcast, critique
            )
            validation = validate_task_graph(task_graph)
            if not validation.valid:
                logger.warning(
                    "Graph validation failed (attempt %d): %s",
                    orchestrator_retries + 1,
                    validation.critique(),
                )
                critique = validation.critique()
                task_graph = None
                orchestrator_retries += 1
            else:
                logger.info(
                    "Graph validation passed (%d tasks, attempt %d)",
                    len(task_graph.tasks),
                    orchestrator_retries + 1,
                )

        if task_graph is None:
            # Give up after 2 retries — use whatever the last attempt produced
            logger.error("Graph validation failed after 2 retries — proceeding anyway")
            task_graph = await _decompose(query, run_id, manager.broadcast, None)

        state.task_graph = task_graph
        state.status = TaskStatus.running

        # Persist the decomposition and materialize Step rows, then mark running.
        await db.save_task_graph(run_id, task_graph.model_dump())
        await db.set_run_status(run_id, "running")

        # ── Step 2: Fan-out workers (respecting dependencies) ─────────────────
        completed_ids: set[str] = set()
        pending_tasks = list(task_graph.tasks)
        _running_tasks[run_id] = {}

        while pending_tasks:
            # Find all tasks whose dependencies are satisfied
            ready = [
                t for t in pending_tasks
                if all(dep in completed_ids for dep in t.dependencies)
            ]
            if not ready:
                break

            for t in ready:
                pending_tasks.remove(t)

            async def run_one(task):
                # Writing worker gets enriched context (table rows, citation
                # snippets, extracted data) so it can produce substantive prose.
                # All other workers only need the brief result string.
                if task.type == TaskType.writing:
                    context = _build_writing_context(task, state.results)
                else:
                    context = {
                        dep: state.results[dep].result
                        for dep in task.dependencies
                        if dep in state.results
                    }
                # Wrap in a named asyncio.Task so /kill can cancel it
                inner = asyncio.create_task(
                    execute_task_with_validation(
                        task=task,
                        run_id=run_id,
                        broadcast=manager.broadcast,
                        context=context or None,
                        validator_enabled=validator_enabled,
                    ),
                    name=f"{run_id}:{task.id}",
                )
                _running_tasks[run_id][task.id] = inner
                try:
                    result = await inner
                except asyncio.CancelledError:
                    # Kill was requested for this task — create a killed AgentResult
                    # and broadcast the event (execute_task never got to do it).
                    result = AgentResult(
                        task_id=task.id,
                        status=TaskStatus.killed,
                        result="Task cancelled by user.",
                        confidence=0.0,
                        model_used="n/a",
                        hardware="n/a",
                        latency_ms=0.0,
                    )
                    await manager.broadcast(run_id, SwarmEvent(
                        event=EventType.task_killed,
                        run_id=run_id,
                        payload={"task_id": task.id},
                    ))
                finally:
                    _running_tasks[run_id].pop(task.id, None)

                state.results[task.id] = result
                # Persist the step's terminal state + a summary attempt row.
                await db.set_step_status(
                    run_id, task.id, result.status.value,
                    result={"text": result.result,
                            "artifacts": [a.model_dump() for a in result.artifacts]},
                    confidence=result.confidence,
                    latency_ms=result.latency_ms,
                    total_attempts=getattr(result, "total_attempts", 1) or 1,
                )
                await db.record_attempt(
                    run_id, task.id,
                    attempt_no=getattr(result, "total_attempts", 1) or 1,
                    status="completed" if result.status == TaskStatus.completed else "failed",
                    result={"text": result.result},
                    model_id=result.model_used,
                    tokens_out=result.total_tokens or None,
                    latency_ms=result.latency_ms,
                )
                # Record metrics (killed tasks still count)
                tasks_total.labels(
                    status=result.status.value,
                    type=task.type.value,
                    hardware=result.hardware,
                ).inc()
                task_latency_seconds.labels(
                    type=task.type.value,
                    hardware=result.hardware,
                ).observe(result.latency_ms / 1000)
                return task.id

            finished_ids = await asyncio.gather(
                *[run_one(t) for t in ready],
                return_exceptions=True,  # one killed task doesn't abort the batch
            )
            completed_ids.update(
                fid for fid in finished_ids if isinstance(fid, str)
            )

        # ── Step 3: Reduce ────────────────────────────────────────────────────
        final_answer, document = await synthesize(
            query=query,
            results=state.results,
            task_graph=task_graph,
            run_id=run_id,
            broadcast=manager.broadcast,
        )
        state.final_answer = final_answer
        state.status = TaskStatus.completed
        state.completed_at = datetime.utcnow()

        latency_ms = (time.perf_counter() - t0) * 1000
        run_latency_seconds.observe(latency_ms / 1000)

        await manager.broadcast(
            run_id,
            SwarmEvent(
                event=EventType.run_completed,
                run_id=run_id,
                payload={
                    "final_answer": final_answer,
                    "latency_ms": latency_ms,
                    "task_count": len(state.results),
                },
            ),
        )

        # Persist result (include structured document if produced)
        if run_id in _run_results:
            _run_results[run_id].swarm = state
            if document:
                _run_results[run_id].document = document
        else:
            _run_results[run_id] = RunResult(
                run_id=run_id, swarm=state, document=document
            )

        # Durable finalize: store the document + metrics and mark completed.
        await db.finalize_run(
            run_id,
            document_result=document.model_dump() if document else None,
            metrics={
                "latency_ms": latency_ms,
                "task_count": len(state.results),
            },
            status="completed",
        )

        # Complete the Langfuse trace (no-op when unconfigured).
        lf.complete_run_trace(
            run_id, output=final_answer,
            metrics={"latency_ms": latency_ms, "task_count": len(state.results)},
            status="completed",
        )

        # Fire-and-forget async quality eval — scores each step against its
        # deliverable_format rubric, persists into run.metrics, broadcasts.
        # Never blocks run completion.
        from backend.evals.runner import evaluate_run
        asyncio.create_task(evaluate_run(run_id, broadcast=manager.broadcast))

    except Exception as exc:
        await db.set_run_status(run_id, "failed", error=str(exc))
        await manager.broadcast(
            run_id,
            SwarmEvent(
                event=EventType.error,
                run_id=run_id,
                payload={"error": str(exc)},
            ),
        )
    finally:
        active_runs.dec()


def launch_run(
    query: str,
    *,
    validator_enabled: bool = True,
    job_id: str | None = None,
    trigger: str = "manual",
) -> str:
    """Create a run_id and kick off run_swarm in the background. Returns run_id.

    Shared entry point for ad-hoc /run, scheduled fires, and /jobs/{id}/run-now,
    so they all go through identical pipeline + persistence paths.
    """
    run_id = str(uuid.uuid4())
    asyncio.create_task(run_swarm(
        run_id, query,
        validator_enabled=validator_enabled,
        job_id=job_id,
        trigger=trigger,
    ))
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
async def kill_task(run_id: str, request: KillTaskRequest):
    """
    Cancel a running worker task by task_id.
    The asyncio.Task is cancelled; the CancelledError is caught in run_swarm()
    which broadcasts task_killed and records a killed AgentResult.
    """
    task = _running_tasks.get(run_id, {}).get(request.task_id)
    if task and not task.done():
        task.cancel()
        return {"status": "killed", "task_id": request.task_id}
    return {"status": "not_found", "task_id": request.task_id}


@app.post("/run/{run_id}/retry")
async def retry_task(run_id: str, request: KillTaskRequest):
    """
    Re-dispatch a single failed or killed task within an existing run.
    Looks up the task from the stored task_graph, rebuilds its dependency context,
    and re-executes it — broadcasting task_started / task_completed as normal.
    This lets the UI retry individual specialists without restarting the full run.
    """
    stored = _run_results.get(run_id)
    if not stored or not stored.swarm.task_graph:
        return {"status": "not_found", "detail": "run or task graph not found"}

    task_spec = next(
        (t for t in stored.swarm.task_graph.tasks if t.id == request.task_id), None
    )
    if not task_spec:
        return {"status": "not_found", "detail": f"task {request.task_id} not in graph"}

    async def _do_retry():
        if task_spec.type == TaskType.writing:
            context = _build_writing_context(task_spec, stored.swarm.results)
        else:
            context = {
                dep: stored.swarm.results[dep].result
                for dep in task_spec.dependencies
                if dep in stored.swarm.results
            }
        inner = asyncio.create_task(
            execute_task(
                task=task_spec,
                run_id=run_id,
                broadcast=manager.broadcast,
                context=context or None,
            ),
            name=f"{run_id}:{task_spec.id}:retry",
        )
        _running_tasks.setdefault(run_id, {})[task_spec.id] = inner
        try:
            result = await inner
        except asyncio.CancelledError:
            result = AgentResult(
                task_id=task_spec.id,
                status=TaskStatus.killed,
                result="Retry cancelled by user.",
                confidence=0.0,
                model_used="n/a",
                hardware="n/a",
                latency_ms=0.0,
            )
            await manager.broadcast(run_id, SwarmEvent(
                event=EventType.task_killed,
                run_id=run_id,
                payload={"task_id": task_spec.id},
            ))
        finally:
            _running_tasks.get(run_id, {}).pop(task_spec.id, None)
        stored.swarm.results[task_spec.id] = result

    asyncio.create_task(_do_retry())
    return {"status": "retrying", "task_id": request.task_id}


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
