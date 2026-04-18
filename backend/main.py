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
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from backend.schemas.models import (
    RunRequest,
    RunResult,
    SwarmEvent,
    SwarmState,
    TaskStatus,
    EventType,
)
from backend.agents.orchestrator import orchestrate_with_events
from backend.agents.worker import execute_task
from backend.agents.reducer import synthesize
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _task_queue
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6479")
    _task_queue = TaskQueue(redis_url)
    yield


app = FastAPI(title="Xeon Agent Swarm Demo", lifespan=lifespan)
app.include_router(corpus_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

async def run_swarm(run_id: str, query: str):
    """Full swarm pipeline: orchestrate → parallel workers → reduce."""
    t0 = time.perf_counter()
    active_runs.inc()
    runs_total.inc()

    state = SwarmState(run_id=run_id, query=query)

    await manager.broadcast(
        run_id,
        SwarmEvent(
            event=EventType.run_started,
            run_id=run_id,
            payload={"query": query},
        ),
    )

    try:
        # ── Step 1: Orchestrate ───────────────────────────────────────────────
        task_graph = await orchestrate_with_events(query, run_id, manager.broadcast)
        state.task_graph = task_graph
        state.status = TaskStatus.running

        # ── Step 2: Fan-out workers (respecting dependencies) ─────────────────
        completed_ids: set[str] = set()
        pending_tasks = list(task_graph.tasks)

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

            # Build dep context for tasks that have dependencies
            async def run_one(task):
                context = {
                    dep: state.results[dep].result
                    for dep in task.dependencies
                    if dep in state.results
                }
                result = await execute_task(
                    task=task,
                    run_id=run_id,
                    broadcast=manager.broadcast,
                    context=context or None,
                )
                state.results[task.id] = result
                # Record metrics
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

            finished_ids = await asyncio.gather(*[run_one(t) for t in ready])
            completed_ids.update(finished_ids)

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
    asyncio.create_task(run_swarm(run_id, request.query))
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
