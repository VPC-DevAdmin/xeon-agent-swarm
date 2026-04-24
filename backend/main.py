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
from backend.agents.worker import execute_task_with_validation
from backend.agents.reducer import synthesize
from backend.graph.swarm_graph import validate_task_graph
from backend.agents.single_model import run_single_model
from backend.protocols.a2a_cards import all_agent_cards, ORCHESTRATOR_CARD
from backend.corpus_api import router as corpus_router
from backend.queue.task_queue import TaskQueue
from backend.observability.metrics import (
    runs_total,
    run_latency_seconds,
    active_runs,
    single_model_latency_seconds,
    tasks_total,
    task_latency_seconds,
    websocket_connections,
)

import time


# ── In-memory store for run results (production: use Redis) ──────────────────
_run_results: dict[str, RunResult] = {}
_task_queue: TaskQueue | None = None

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
    global _task_queue
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6479")
    _task_queue = TaskQueue(redis_url)
    yield


import logging
logger = logging.getLogger(__name__)

app = FastAPI(title="Xeon Agent Swarm Demo", lifespan=lifespan)
app.include_router(corpus_router)


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
        for ws in list(self.connections.get(run_id, [])):
            try:
                await ws.send_text(event.model_dump_json())
            except Exception:
                pass


manager = ConnectionManager()


# ── Swarm pipeline ───────────────────────────────────────────────────────────

async def run_swarm(run_id: str, query: str, validator_enabled: bool = True):
    """Full swarm pipeline: orchestrate → validate graph → parallel workers → reduce."""
    t0 = time.perf_counter()
    active_runs.inc()
    runs_total.inc()

    state = SwarmState(run_id=run_id, query=query, validator_enabled=validator_enabled)

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
            task_graph = await orchestrate_with_events(
                query, run_id, manager.broadcast, critique=critique
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
            task_graph = await orchestrate_with_events(query, run_id, manager.broadcast)

        state.task_graph = task_graph
        state.status = TaskStatus.running

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

    except Exception as exc:
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


async def run_single_model_pipeline(run_id: str, query: str):
    """A/B single-model pipeline — runs concurrently with run_swarm."""
    t0 = time.perf_counter()
    try:
        result = await run_single_model(run_id=run_id, query=query, broadcast=manager.broadcast)
        single_model_latency_seconds.observe((time.perf_counter() - t0))

        # Persist result
        if run_id in _run_results:
            _run_results[run_id].single_model = result
        else:
            _run_results[run_id] = RunResult(
                run_id=run_id,
                swarm=SwarmState(run_id=run_id, query=query),
                single_model=result,
            )
    except Exception as exc:
        await manager.broadcast(
            run_id,
            SwarmEvent(
                event=EventType.error,
                run_id=run_id,
                payload={"error": f"single_model: {exc}"},
            ),
        )


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.post("/run")
async def start_run(request: RunRequest):
    """Start a new swarm run. Set ENABLE_AB_COMPARISON=1 to also start
    the single-model baseline pipeline for legacy A/B comparison mode."""
    run_id = str(uuid.uuid4())
    asyncio.create_task(run_swarm(
        run_id,
        request.query,
        validator_enabled=request.validator_enabled,
    ))
    if os.getenv("ENABLE_AB_COMPARISON", "").strip() == "1":
        asyncio.create_task(run_single_model_pipeline(run_id, request.query))
    return {"run_id": run_id}


@app.get("/run/{run_id}")
async def get_run(run_id: str):
    """Fetch the final RunResult (may be incomplete if still running)."""
    result = _run_results.get(run_id)
    if result:
        return result
    # Try Redis
    if _task_queue:
        cached = await _task_queue.get_run_result(run_id)
        if cached:
            return cached
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
