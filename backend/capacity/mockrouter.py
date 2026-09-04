"""
Pre-flight + auto-start for the bundled mock tier router (scripts/mock_router.py).

Agent-host capacity tests with the remote_mock backend drive real orchestrator
workflows against the mock router on :8901. On a laptop `make demo` already runs
it; on the demo box nothing does — so the capacity API probes the endpoint and,
when it is a loopback address, spawns the bundled script itself. A non-loopback
URL is never auto-started (it is someone else's service): the caller gets a
clear error instead.

The child is a plain subprocess owned by this process: it dies with the backend
and is reused across runs. Nothing else manages it — this is a benchmark
convenience, not a production service.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mock_router.py"
_proc: subprocess.Popen | None = None


async def _serving(base_url: str) -> bool:
    probe = base_url.rstrip("/").removesuffix("/v1") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(probe)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — any failure means "not serving"
        return False


def _is_loopback(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def metadata(base_url: str | None) -> dict | None:
    """Facts needed to judge whether the inference stand-in was isolated."""
    if not base_url:
        return None
    loopback = _is_loopback(base_url)
    certified = os.getenv("CAPACITY_MOCK_CERTIFIED_RPS", "").strip()
    return {
        "base_url": base_url,
        "loopback": loopback,
        "isolated_from_host": not loopback,
        "spawned_by_benchmark": bool(_proc is not None and _proc.poll() is None),
        "workers": int(os.getenv("MOCK_ROUTER_WORKERS", "4")),
        "latency_ms": float(os.getenv("MOCK_LATENCY_MS", "0") or 0),
        "latency_sigma_ms": float(os.getenv("MOCK_LATENCY_SIGMA_MS", "0") or 0),
        # The modeled serving tier: per-call wait = ttft + out/decode +
        # in/prefill, computed from actual payloads (zero = instant stand-in).
        "model_ttft_ms": float(os.getenv("CAPACITY_MODEL_TTFT_MS", "0") or 0),
        "model_decode_tps": float(os.getenv("CAPACITY_MODEL_DECODE_TPS", "0") or 0),
        "model_prefill_tps": float(os.getenv("CAPACITY_MODEL_PREFILL_TPS", "0") or 0),
        # A recorded serving profile replaces the modeled tier when set.
        "serving_profile": os.getenv("CAPACITY_SERVING_PROFILE"),
        "serving_concurrency": os.getenv("CAPACITY_SERVING_CONCURRENCY"),
        # Supplied only after an independent endpoint diagnostic.  Agent-host
        # results require twice the observed model-call demand as headroom.
        "certified_requests_per_s": float(certified) if certified else None,
    }


async def ensure_mock_router(base_url: str) -> None:
    """Make sure the mock router answers at base_url, spawning it if local.

    Raises RuntimeError with an operator-actionable message when it cannot.
    """
    global _proc
    if await _serving(base_url):
        return
    if not _is_loopback(base_url):
        raise RuntimeError(
            f"mock router is not serving at {base_url} — start it there or "
            "point CAPACITY_AGENT_HOST_MOCK_BASE_URL at a running instance")
    if _proc is not None and _proc.poll() is not None:
        _proc = None                      # previous child exited; forget it
    if _proc is None:
        port = urlparse(base_url).port or 8901
        workers = os.getenv("MOCK_ROUTER_WORKERS", "4")
        logger.info("starting bundled mock router on :%s (%s workers)", port, workers)
        # Multi-worker: the mock stands in for the LLM tier, and a single
        # process saturates near ~1k req/s — well under what 2,000 agent
        # sessions generate. It is stateless, so workers scale it linearly.
        _proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "scripts.mock_router:app",
             "--host", "127.0.0.1", "--port", str(port),
             "--workers", workers, "--log-level", "warning"],
            cwd=str(_SCRIPT.parents[1]), env={**os.environ},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    for _ in range(20):                   # ~10s for uvicorn to bind
        await asyncio.sleep(0.5)
        if await _serving(base_url):
            return
        if _proc.poll() is not None:
            code = _proc.poll()
            _proc = None
            raise RuntimeError(
                f"bundled mock router exited immediately (code {code}) — "
                f"try `python {_SCRIPT}` by hand to see why")
    raise RuntimeError(
        f"mock router did not become ready at {base_url} within 10s")
