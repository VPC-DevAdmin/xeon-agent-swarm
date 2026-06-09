"""
Optional Langfuse tracing.

Langfuse gives an agent-aware trace UI (per-run traces with token costs,
latencies, scores, prompt management, and offline evals). It is entirely
optional: when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY aren't set, every
function here is a cheap no-op and the platform runs unchanged.

The integration is deliberately defensive about the Langfuse SDK version — its
API has shifted across v2/v3, so every call is wrapped in try/except and a
failure only drops the trace, never the run.

Self-hosting: docker-compose.langfuse.yml brings up the Langfuse stack.
Set LANGFUSE_HOST=http://langfuse-web:3000 and the keys, then restart backend.
"""
from __future__ import annotations

import logging
import os

from backend.observability.trace import trace_id_for_run

logger = logging.getLogger(__name__)

_client = None
_initialized = False


def is_enabled() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    )


def get_client():
    """Lazily build the Langfuse client, or None if unconfigured/unavailable."""
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    if not is_enabled():
        return None
    try:
        from langfuse import Langfuse  # type: ignore
        _client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        )
        logger.info("Langfuse tracing enabled (host=%s)", os.getenv("LANGFUSE_HOST"))
    except Exception as exc:
        logger.warning("Langfuse init failed — tracing disabled: %s", exc)
        _client = None
    return _client


def trace_url(run_id: str) -> str | None:
    """Best-effort deep link to the run's trace in the Langfuse UI."""
    if not is_enabled():
        return None
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    return f"{host}/trace/{trace_id_for_run(run_id)}"


def start_run_trace(run_id: str, query: str, config: dict | None = None) -> str | None:
    """Create a trace for a run. Returns the trace_id (our deterministic id) or None."""
    client = get_client()
    if client is None:
        return None
    tid = trace_id_for_run(run_id)
    try:
        # SDK-version tolerant: prefer .trace(), fall back to no-op on mismatch.
        if hasattr(client, "trace"):
            client.trace(id=tid, name="swarm_run", input={"query": query},
                         metadata=config or {})
    except Exception as exc:
        logger.debug("langfuse start_run_trace failed: %s", exc)
    return tid


def complete_run_trace(
    run_id: str, *, output=None, metrics: dict | None = None, status: str = "completed"
) -> None:
    client = get_client()
    if client is None:
        return
    tid = trace_id_for_run(run_id)
    try:
        if hasattr(client, "trace"):
            client.trace(id=tid, output=output,
                         metadata={"status": status, **(metrics or {})})
        if hasattr(client, "flush"):
            client.flush()
    except Exception as exc:
        logger.debug("langfuse complete_run_trace failed: %s", exc)


def record_span(run_id: str, name: str, payload: dict | None = None) -> None:
    """Log a span/event under the run's trace (best-effort)."""
    client = get_client()
    if client is None:
        return
    tid = trace_id_for_run(run_id)
    try:
        if hasattr(client, "span"):
            client.span(trace_id=tid, name=name, input=payload or {})
    except Exception as exc:
        logger.debug("langfuse record_span failed: %s", exc)
