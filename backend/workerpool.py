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
import time as _time
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
callback_failures = 0                 # completion callbacks lost after retry
callback_failure_times: list[float] = []   # when each loss happened (see below)
_clients: dict[str, httpx.AsyncClient] = {}


def _http(url: str) -> httpx.AsyncClient:
    # One client PER ORIGIN, not one shared pool. httpcore rescans its whole
    # connection list on every request event, so a single 2,000-connection
    # pool cost ~half the control plane's event loop at the boundary (py-spy,
    # 2026-08-30): _assign_requests_to_connections and its is_idle/has_expired
    # probes were 45-50% of all samples at 1,167 sessions. Sharding by origin
    # keeps each scan at pool size, and per-origin limits still clear the
    # burst profile: an AIMD batch fans out across every executor, so no
    # single origin sees more than its share of a burst. (The old shared
    # 100-connection default failed dispatches at 1,242 sessions; the
    # per-origin cap below is sized well above one origin's share of that.)
    origin = url.split("/", 3)[2] if "//" in url else url
    client = _clients.get(origin)
    if client is None:
        client = _clients[origin] = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=128,
                                max_keepalive_connections=64))
    return client


def _free_ports(base: int, count: int, avoid: set[int]) -> list[int]:
    """The first `count` bindable ports from `base`, skipping occupied ones.

    A widened pool walks into other tenants: at 96 workers the naive
    base+i range crossed the voice demo's gateway on 8080 and a neighbour
    on 8083, those executors died at bind with nothing logged, and the
    pool silently ran 94-strong until run-level integrity refused the
    result. Probe first, skip what is taken, and say so.
    """
    import socket
    out: list[int] = []
    candidate = base
    while len(out) < count and candidate < base + 10 * count:
        if candidate not in avoid:
            try:
                with socket.socket() as s_:
                    s_.bind(("127.0.0.1", candidate))
                out.append(candidate)
            except OSError:
                logger.warning("executor port %d is taken by another "
                               "process — skipping it", candidate)
        candidate += 1
    if len(out) < count:
        raise RuntimeError(f"could not find {count} free executor ports "
                           f"from {base}")
    return out


async def start_pool() -> None:
    """Spawn the executor processes and wait until each answers /health."""
    n = worker_count()
    tok = internal_token()
    port = int(os.getenv("PORT", "8010"))
    ports = _free_ports(BASE_PORT, n, avoid={port})
    for i in range(n):
        wport = ports[i]
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
                r = await _http(url).get(f"{url}/health")
                if r.status_code == 200:
                    pending.discard(url)
            except Exception:
                pass
        if pending:
            await asyncio.sleep(0.5)
    if pending:
        # A pool that is not the pool it claims to be must be LOUD: run-level
        # integrity will refuse every result until this is fixed, so the
        # operator should hear it at startup, not at reconciliation.
        logger.error("executor(s) not healthy after 30s (results will be "
                     "refused by harness integrity): %s", sorted(pending))
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
    """Dispatch with retry: a transient client-pool or socket hiccup must not
    turn into a failed run when the executor is perfectly healthy."""
    last: Exception | None = None
    for delay in (0.0, 0.2, 1.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            r = await _http(url).post(f"{url}/internal/run", json=payload,
                                   headers={"X-Internal-Token": internal_token()})
            r.raise_for_status()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last


_event_buf: list[dict] = []
_event_flusher: asyncio.Task | None = None
_EVENT_BUF_MAX = 10_000          # drop-oldest beyond this: relay is liveness
_EVENT_FLUSH_S = 0.1


async def _event_flush_loop():
    while True:
        await asyncio.sleep(_EVENT_FLUSH_S)
        if not _event_buf:
            continue
        batch, _event_buf[:] = _event_buf[:], []
        try:
            await _http(control_url()).post(f"{control_url()}/internal/events_batch",
                               json={"events": batch},
                               headers={"X-Internal-Token": internal_token()})
        except Exception:
            logger.debug("event relay batch of %d dropped", len(batch))


async def forward_event(run_id: str, event_json: dict) -> None:
    """Executor -> control: buffer the WS event; a flusher POSTs batches every
    100ms. Best-effort — the DB rows are the durable record; a dropped relay
    only costs liveness. Per-event POSTs were ~2.5k req/s at 2,000 sessions."""
    global _event_flusher
    if _event_flusher is None or _event_flusher.done():
        _event_flusher = asyncio.get_event_loop().create_task(_event_flush_loop())
    if len(_event_buf) >= _EVENT_BUF_MAX:
        del _event_buf[:len(_event_buf) // 2]
    _event_buf.append({"run_id": run_id, "event": event_json})


async def post_complete(payload: dict) -> None:
    """Executor -> control: a dispatched run reached a terminal state. Carries
    the full outcome so the benchmark never has to poll the database.

    Retried, and counted when it fails for good: a lost callback would appear
    to the benchmark as a workflow that never finished, which is a harness
    failure wearing an agent failure's clothes."""
    global callback_failures
    for delay in (0.0, 0.2, 1.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            await _http(control_url()).post(f"{control_url()}/internal/complete", json=payload,
                               headers={"X-Internal-Token": internal_token()})
            return
        except Exception:  # noqa: BLE001
            continue
    callback_failures += 1
    # The TIMESTAMP travels with the count: a callback lost during the
    # collapse of an already-condemned level taints nothing that gets
    # published, while one lost during evidence-gathering taints everything.
    # Only the controller knows which phase a moment belonged to, so the
    # executor records when and the controller judges what it meant.
    callback_failure_times.append(_time.time())
    del callback_failure_times[:-200]
    logger.warning("completion callback lost for run %s", payload.get("run_id"))


async def collect_counters() -> dict:
    """Harness integrity counters for this process and every executor."""
    from backend.repositories import persistence
    totals = {"persist_failures": persistence.failure_count(),
              "callback_failures": callback_failures,
              "callback_failure_times": list(callback_failure_times),
              "unreachable_executors": 0}
    for url in _urls:
        try:
            r = await _http(url).get(f"{url}/internal/counters",
                                  headers={"X-Internal-Token": internal_token()})
            body = r.json()
            totals["persist_failures"] += int(body.get("persist_failures") or 0)
            totals["callback_failures"] += int(body.get("callback_failures") or 0)
            totals["callback_failure_times"].extend(
                float(t) for t in (body.get("callback_failure_times") or []))
        except Exception:  # noqa: BLE001
            totals["unreachable_executors"] += 1
    return totals


async def proxy_post(url: str, path: str, body: dict | None = None) -> dict:
    r = await _http(url).post(f"{url}{path}", json=body or {},
                           headers={"X-Internal-Token": internal_token()})
    r.raise_for_status()
    return r.json()
