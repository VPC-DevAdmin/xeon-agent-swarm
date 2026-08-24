"""
Multi-process orchestrator: a control process plus N run-executor processes.

Why: the orchestrator is asyncio in ONE Python process, so the GIL caps it at
~one core. Measured on the 64-core box: agent-host capacity certified at 6-9
concurrent workflows with the backend process pegging a single core and the
host at 2% CPU. Execution must spread across processes for capacity numbers to
reflect the hardware.

Topology (opt-in via ADL_WORKERS=N; 0 keeps today's single-process behavior):

  control process (:8010, public)      executors (127.0.0.1:BASE+i, private)
    REST + WebSockets + scheduler        run_deepagents() for dispatched runs
    HITL routing + capacity controller   POST /internal/run   (token-guarded)
    spawns/owns the executor pool        events relayed back to the control
                                         process for the live UI

Shared state rides the WAL SQLite files (runs/steps/validations) — the DB was
already the system of record; the pool just adds dispatch + event relay:

  dispatch   control -> executor   POST /internal/run {run_id, query, opts}
  events     executor -> control   POST /internal/events {run_id, event}
  kill/HITL  control -> executor   proxied to the owning executor's endpoint

Both directions carry X-Internal-Token (generated at control startup, handed
to executors via env) because the control process is publicly reachable
through the tunnel. Executors bind 127.0.0.1 only.

Known limits (v1, documented not solved): an executor crash strands its runs
as "running"; Prometheus metrics are per-process; the run→executor map lives
in control-process memory, so a control restart orphans kill/approve routing
for in-flight runs (they die with the pool on a service restart anyway).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import secrets
import subprocess
import sys

import httpx

logger = logging.getLogger(__name__)

BASE_PORT = int(os.getenv("ADL_WORKER_BASE_PORT", "8011"))


def worker_count() -> int:
    try:
        return max(0, int(os.getenv("ADL_WORKERS", "0") or 0))
    except ValueError:
        return 0


def is_worker() -> bool:
    return os.getenv("XEON_ROLE", "").strip().lower() == "worker"


def pool_enabled() -> bool:
    """True on a control process that dispatches to an executor pool."""
    return worker_count() > 0 and not is_worker()


def internal_token() -> str:
    """Shared secret for /internal/* — generated once per control process and
    inherited by its executors through the spawn env."""
    tok = os.environ.get("XEON_INTERNAL_TOKEN")
    if not tok:
        tok = secrets.token_urlsafe(24)
        os.environ["XEON_INTERNAL_TOKEN"] = tok
    return tok


def check_token(header_value: str | None) -> bool:
    return bool(header_value) and header_value == internal_token()


def control_url() -> str:
    return os.getenv("XEON_CONTROL_URL", "http://127.0.0.1:8010")


_procs: list[subprocess.Popen] = []
_urls: list[str] = []
_rr = itertools.count()
_owners: dict[str, str] = {}          # run_id -> executor base_url
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def start_pool() -> None:
    """Spawn the executor processes and wait until each answers /health."""
    n = worker_count()
    tok = internal_token()
    port = int(os.getenv("PORT", "8010"))
    for i in range(n):
        wport = BASE_PORT + i
        env = {
            **os.environ,
            "XEON_ROLE": "worker",
            "XEON_INTERNAL_TOKEN": tok,
            "XEON_CONTROL_URL": f"http://127.0.0.1:{port}",
            "SCHEDULER_ENABLED": "0",     # exactly one scheduler: the control's
            "ADL_WORKERS": "0",           # executors never spawn their own pool
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--host", "127.0.0.1", "--port", str(wport), "--log-level", "warning"],
            env=env,
        )
        _procs.append(proc)
        _urls.append(f"http://127.0.0.1:{wport}")
    deadline = asyncio.get_event_loop().time() + 30
    pending = set(_urls)
    while pending and asyncio.get_event_loop().time() < deadline:
        for url in list(pending):
            try:
                r = await _http().get(f"{url}/health")
                if r.status_code == 200:
                    pending.discard(url)
            except Exception:
                pass
        if pending:
            await asyncio.sleep(0.5)
    if pending:
        logger.warning("executor(s) not healthy after 30s: %s", sorted(pending))
    logger.info("executor pool up: %d process(es) at %s", len(_urls), _urls)


async def stop_pool() -> None:
    for p in _procs:
        if p.poll() is None:
            p.terminate()
    for p in _procs:
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    _procs.clear()
    _urls.clear()
    _owners.clear()


def next_worker() -> str | None:
    """Round-robin over live executors; None when the pool is empty/dead."""
    live = [u for u, p in zip(_urls, _procs) if p.poll() is None]
    if not live:
        return None
    return live[next(_rr) % len(live)]


def assign(run_id: str, url: str) -> None:
    _owners[run_id] = url


def owner(run_id: str) -> str | None:
    return _owners.get(run_id)


async def dispatch_run(url: str, payload: dict) -> None:
    r = await _http().post(f"{url}/internal/run", json=payload,
                           headers={"X-Internal-Token": internal_token()})
    r.raise_for_status()


async def forward_event(run_id: str, event_json: dict) -> None:
    """Executor -> control: relay one WS event for the live UI. Best-effort —
    the DB rows are the durable record; a dropped relay only costs liveness."""
    try:
        await _http().post(f"{control_url()}/internal/events",
                           json={"run_id": run_id, "event": event_json},
                           headers={"X-Internal-Token": internal_token()})
    except Exception:
        logger.debug("event relay to control failed for run %s", run_id)


async def proxy_post(url: str, path: str, body: dict | None = None) -> dict:
    r = await _http().post(f"{url}{path}", json=body or {},
                           headers={"X-Internal-Token": internal_token()})
    r.raise_for_status()
    return r.json()
