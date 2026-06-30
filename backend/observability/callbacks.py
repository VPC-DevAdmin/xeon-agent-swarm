"""
backend/observability/callbacks.py

Per-call route capture. The gateway reports its decision in response headers,
already mapped to tier ids (model identity never leaks):

  x-vsr-selected-model       tier that served it, e.g. tier3
  x-vsr-selected-category    classified category, e.g. math
  x-vsr-selected-reasoning   on | off
  x-vsr-selected-confidence  classifier confidence (when present)
  x-vsr-selected-decision    routing decision detail (when present)
  x-vsr-matched-*            matched signals

These surface in response_metadata["headers"] because the model is built with
include_response_headers=True (see backend/inference/model.py). On a cache hit the
x-vsr-selected-model header is absent; fall back to the body model field, which is
also a tier id.

Requested vs observed: pinned calls already know their tier. Tag the invocation
with "tier_req:<tier>" via the runnable config so both sit in the same row. The
event adapter supplies the `sink` that writes onto the owning Step/Attempt.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler


def to_internal_tier(tier_id: str | None) -> str:
    """Gateway 'tier3' -> internal 'T3'. 'auto'/None -> 'unknown'."""
    if not tier_id:
        return "unknown"
    t = tier_id.strip().lower()
    if t.startswith("tier") and t[4:].isdigit():
        return f"T{t[4:]}"
    return tier_id


class RouteCaptureHandler(BaseCallbackHandler):
    def __init__(self, sink: Callable[[dict], None]) -> None:
        self.sink = sink  # writes one row onto the current Step/Attempt

    def on_llm_end(self, response: Any, *, run_id, parent_run_id=None, tags=None, **kw) -> None:
        message = response.generations[0][0].message
        meta = getattr(message, "response_metadata", {}) or {}
        headers = {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}

        selected = headers.get("x-vsr-selected-model")
        cache_hit = selected is None
        if cache_hit:
            selected = meta.get("model_name") or meta.get("model")

        tier_requested = next(
            (t.split(":", 1)[1] for t in (tags or []) if t.startswith("tier_req:")),
            None,
        )

        self.sink({
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "tags": list(tags or []),
            "tier_requested": tier_requested,                 # 'auto', 'T5', or None
            "tier_observed": to_internal_tier(selected),
            "category": headers.get("x-vsr-selected-category"),
            "reasoning": headers.get("x-vsr-selected-reasoning"),
            "confidence": headers.get("x-vsr-selected-confidence"),
            "decision": headers.get("x-vsr-selected-decision"),
            "matched": {
                k.replace("x-vsr-matched-", ""): v
                for k, v in headers.items()
                if k.startswith("x-vsr-matched-")
            },
            "cache_hit": cache_hit,
            **self._usage(response),
        })

    @staticmethod
    def _usage(response: Any) -> dict:
        out = getattr(response, "llm_output", None) or {}
        usage = out.get("token_usage") or out.get("usage") or {}
        return {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
