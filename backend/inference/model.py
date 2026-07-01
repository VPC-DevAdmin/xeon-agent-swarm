"""
backend/inference/model.py — single seam between this project and the tier router.

Contract: the gateway is the interactive server (default http://localhost:8900),
an OpenAI-compatible Chat Completions endpoint. Do NOT call vllm-sr (:8899)
directly. The `model` field is a TIER SELECTOR, not a model name:

  - model="auto"             -> router classifies the query and picks the tier
  - model="tier1".."tier5"   -> pin that tier (tier1 cheapest, tier5 frontier)

A real model id is rejected with 400. Model identity is owned by server config and
never leaves the gateway, so this project holds no model names and a backend model
swap needs no client change. The response `model` field and the x-vsr-selected-model
header are both already tier ids.

Principle: this layer does not classify queries. Workers go out as "auto" and the
router decides. We pin a tier only for structural roles (planner, synthesis) where
the function needs frontier capability regardless of input. That is orchestration
config, not per-query tier choosing.

Operational notes:
  - Streaming is not supported by the gateway (400). Model-level token streaming is
    disabled here; our own event streaming is a separate concern.
  - Send max_completion_tokens; the gateway auto-retries as max_tokens upstream if
    needed, so we need no retry of our own for the token field.
  - Auth follows SR_AUTH_MODE: open/access need nothing from the client; proxy
    requires X-Auth-Email + X-Proxy-Secret on every request.
  - request_timeout / max_retries are salvaged from the retired inference/client.py:
    the CPU model servers are slow to warm, so a 300s ceiling and a few retries keep
    the first concurrent batch from failing on a cold engine.
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


def to_wire_tier(tier: str) -> str:
    """Normalize an internal tier label to the gateway selector. 'T5' -> 'tier5'."""
    t = tier.strip().lower()
    if t == "auto":
        return "auto"
    if t.startswith("tier"):
        t = t[4:]
    t = t.lstrip("t")
    if t not in {"1", "2", "3", "4", "5"}:
        raise ValueError(f"invalid tier {tier!r}; use 'auto' or T1..T5")
    return f"tier{t}"


class ModelFactory:
    """Produces chat models bound to the gateway. This project never names a model."""

    def __init__(self) -> None:
        base = os.environ.get("ROUTER_BASE", "http://localhost:8900")
        self.base_url = os.environ.get("ROUTER_BASE_URL", f"{base}/v1")
        self.api_key = os.environ.get("ROUTER_API_KEY", "unused")  # gateway ignores it
        self.max_completion_tokens = int(os.environ.get("ADL_MAX_COMPLETION_TOKENS", "2048"))
        # Workers get their own (typically lower) ceiling. A pinned planner needs
        # headroom to emit a full multi-subtask plan and the final synthesis, but a
        # worker on a slow cold-CPU tier must finish a generation under the gateway's
        # 180s upstream timeout — a large worker budget invites a connection drop.
        self.worker_max_completion_tokens = int(
            os.environ.get("ADL_WORKER_MAX_COMPLETION_TOKENS",
                           str(self.max_completion_tokens))
        )
        # Salvaged from inference/client.py — slow CPU warmup tolerance.
        self.request_timeout = float(os.environ.get("ROUTER_REQUEST_TIMEOUT", "300"))
        # The gateway occasionally drops a connection mid-flight (observed live). The
        # OpenAI client retries APIConnectionError with exponential backoff, so a
        # generous default lets a transient blip ride over instead of failing the whole
        # run — a planner-turn drop is otherwise fatal (no plan). Tune via ROUTER_MAX_RETRIES.
        self.max_retries = int(
            os.environ.get("ROUTER_MAX_RETRIES",
                           os.environ.get("INFERENCE_MAX_RETRIES", "6"))
        )
        self._auth_headers = self._build_auth_headers()

    def _build_auth_headers(self) -> dict[str, str]:
        if os.environ.get("SR_AUTH_MODE", "open") == "proxy":
            return {
                "X-Auth-Email": os.environ["SR_AUTH_EMAIL"],
                "X-Proxy-Secret": os.environ["SR_PROXY_SECRET"],
            }
        return {}

    def _make(self, model: str, temperature: float | None,
              max_completion_tokens: int | None = None) -> ChatOpenAI:
        # Temperature is OMITTED unless explicitly requested. The gateway owns model
        # identity, so a tier may resolve to a reasoning model (e.g. Claude/o-series
        # with extended thinking), and those reject temperature != 1 with a 400. The
        # same principle that keeps model names out of this layer keeps sampling
        # params out of it: let the server apply its own default. Callers that truly
        # need determinism on a known non-reasoning tier can pass temperature.
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            model=model,                       # "auto" or "tierN", never a model id
            default_headers=self._auth_headers or None,
            include_response_headers=True,     # surfaces x-vsr-* in response_metadata
            disable_streaming=True,            # gateway rejects stream
            timeout=self.request_timeout,      # cold-CPU ceiling (salvaged)
            max_retries=self.max_retries,      # cold-engine warmup retries (salvaged)
            model_kwargs={
                "max_completion_tokens": max_completion_tokens or self.max_completion_tokens
            },
            **kwargs,
        )

    def auto(self, temperature: float | None = None) -> ChatOpenAI:
        """Worker default: let the router classify and choose the tier."""
        return self._make("auto", temperature, self.worker_max_completion_tokens)

    def for_tier(self, tier: str, temperature: float | None = None) -> ChatOpenAI:
        """Pin a structural role to a tier, e.g. for_tier('T5') -> model='tier5'."""
        return self._make(to_wire_tier(tier), temperature)
