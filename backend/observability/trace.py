"""
W3C TraceContext helpers.

We propagate a single trace-id from run start through every LLM router call so
the router (and downstream model servers) can stitch their spans into the same
trace. See docs/standards.md §2.3 and docs/router-contract.md §6.3.

This module deliberately avoids a hard dependency on the OpenTelemetry SDK so
the header plumbing works even when no tracer is configured. If/when the OTel
SDK is wired up, these helpers can read the active span context instead of
generating ids from the run_id.

A traceparent header looks like:
    00-<32 hex trace-id>-<16 hex parent-id>-01
    ^version          ^trace-id        ^span-id  ^flags(sampled)
"""
from __future__ import annotations

import hashlib

_VERSION = "00"
_FLAGS = "01"  # sampled


def _hex(seed: str, length: int) -> str:
    """Deterministic hex string of the given length, derived from seed."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:length]


def trace_id_for_run(run_id: str) -> str:
    """Derive a stable 32-hex W3C trace-id from a run_id.

    Deterministic so the same run always maps to the same trace-id (useful for
    re-runs and for correlating logs without storing a separate mapping).
    """
    return _hex(f"run:{run_id}", 32)


def traceparent(run_id: str, span_label: str) -> str:
    """Build a W3C traceparent header for an outbound router call.

    run_id     → the trace-id (one trace per run)
    span_label → e.g. "orchestrate", "validate:t1", "worker:t3" — derives the
                 16-hex parent span-id so distinct calls get distinct spans.
    """
    tid = trace_id_for_run(run_id)
    span_id = _hex(f"{run_id}:{span_label}", 16)
    return f"{_VERSION}-{tid}-{span_id}-{_FLAGS}"


def trace_headers(run_id: str, span_label: str) -> dict[str, str]:
    """Convenience: the dict to pass as extra_headers on an InferenceClient call."""
    return {"traceparent": traceparent(run_id, span_label)}
