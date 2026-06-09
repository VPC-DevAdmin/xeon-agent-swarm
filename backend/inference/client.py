from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import AsyncGenerator, Type, TypeVar

import httpx
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Hard wall on any single inference call.
# Reasoning:
#   - fact_check (400 tok)   @ ~8 tok/s CPU  → ~50s  ← safe under 300s
#   - research/analysis      @ ~8 tok/s CPU  → ~150s ← safe
#   - writing (2000 tok)     @ ~8 tok/s CPU  → ~250s ← needs headroom
# 300s is a realistic ceiling for any role given their capped token budgets.
_INFERENCE_TIMEOUT = httpx.Timeout(timeout=300.0, connect=30.0)

# Retry transient transport errors (httpx connection reset, pool exhaustion,
# APIConnectionError, APITimeoutError).  These commonly happen on the first
# few concurrent requests against a freshly-warmed vLLM engine — the server
# is listening but the inductor compile for the first forward pass is still
# running, so concurrent calls race and some get cut.
_MAX_RETRIES = int(os.getenv("INFERENCE_MAX_RETRIES", "3"))
_RETRY_BACKOFF_BASE = 2.0  # seconds; 2, 4, 8 … with ±25% jitter
_RETRYABLE = (APIConnectionError, APITimeoutError,
              httpx.ConnectError, httpx.ReadError,
              httpx.RemoteProtocolError, httpx.PoolTimeout)


# ── Router endpoint + model resolution ───────────────────────────────────────
# Single source of truth for "where do we send LLM calls" and "which model
# specialty does this role request." Callers (orchestrator, validator, worker,
# reducer) use llm_endpoint() and llm_model_for(role) instead of re-doing the
# env-var fallback chain inline.

_ROLE_ENV = {
    "orchestrator": "ORCHESTRATOR_MODEL",
    "validator":    "VALIDATOR_MODEL",
    "worker":       "WORKER_DEFAULT_MODEL",
    "reducer":      "REDUCER_MODEL",
}

# Router specialty defaults — see docs/router-contract.md §2.1
_ROLE_DEFAULT_SPECIALTY = {
    "orchestrator": "orchestrator-v2.1",
    "validator":    "validator-v1.0",
    "worker":       "worker-default-v1.0",
    "reducer":      "worker-default-v1.0",  # writing role uses the default worker
}


def llm_endpoint() -> str:
    """Resolve the LLM router endpoint.

    Order of precedence:
      1. LLM_TIER_ENDPOINT  (canonical; the external router)
      2. TEXT_ENGINE_ENDPOINT  (legacy single-engine name)
      3. ORCHESTRATOR_ENDPOINT  (oldest legacy)
      4. http://localhost:8080/v1  (last-resort default)
    """
    return (
        os.getenv("LLM_TIER_ENDPOINT")
        or os.getenv("TEXT_ENGINE_ENDPOINT")
        or os.getenv("ORCHESTRATOR_ENDPOINT")
        or "http://localhost:8080/v1"
    )


def llm_model_for(role: str) -> str:
    """Resolve the model / specialty name for a role.

    Role-specific env var (ORCHESTRATOR_MODEL, VALIDATOR_MODEL, etc.) wins;
    falls back to legacy TEXT_ENGINE_MODEL, then to the router contract's
    default specialty for the role.
    """
    role_env = _ROLE_ENV.get(role)
    return (
        (role_env and os.getenv(role_env))
        or os.getenv("TEXT_ENGINE_MODEL")
        or os.getenv("ORCHESTRATOR_MODEL")
        or _ROLE_DEFAULT_SPECIALTY.get(role, "worker-default-v1.0")
    )


async def _retry(coro_factory, *, label: str):
    """Call coro_factory() with retry on transient transport errors.

    coro_factory is a zero-arg async callable so each attempt creates a fresh
    coroutine (coroutines can't be awaited twice).
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                logger.error("[%s] giving up after %d attempts: %s",
                             label, attempt, exc)
                raise
            delay = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            delay *= random.uniform(0.75, 1.25)
            logger.warning("[%s] transient error %r — retry %d/%d in %.1fs",
                           label, exc, attempt, _MAX_RETRIES - 1, delay)
            await asyncio.sleep(delay)
    # Unreachable — loop either returns or raises on the final attempt
    assert last_exc is not None
    raise last_exc

# Concurrency semaphore for the shared text engine.
# Limits simultaneous requests to avoid overwhelming the single Mistral-7B engine.
# Workers, validator, and reducer all share this semaphore.
# Orchestrator and vision bypass it (they run at different times or on separate engines).
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "8"))
_worker_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily initialize the semaphore (must be created inside a running event loop)."""
    global _worker_semaphore
    if _worker_semaphore is None:
        _worker_semaphore = asyncio.Semaphore(WORKER_CONCURRENCY)
    return _worker_semaphore


class InferenceClient:
    """
    Wraps an OpenAI-compatible endpoint (the LLM router — see docs/router-contract.md).

    Structured output uses the router's native OpenAI Structured Outputs support
    (`response_format: {type: "json_schema", strict: true}`), which the router
    guarantees via server-side grammar-constrained decoding. No instructor
    library, no MD_JSON workaround, no retry-on-malformed-JSON — the router
    enforces the schema at the token level.

    use_semaphore=True: acquire the global WORKER_CONCURRENCY semaphore before
    every call. All text workers, the validator, and the reducer should set this.
    Orchestrator and vision engine clients set use_semaphore=False.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        hardware: str = "cpu",
        use_semaphore: bool = False,
    ):
        api_key = os.getenv("LLM_TIER_TOKEN") or "none"
        self._raw = AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=_INFERENCE_TIMEOUT
        )
        self.model = model
        self.hardware = hardware
        self.use_semaphore = use_semaphore

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, float]:
        """Plain completion. Returns (content, latency_ms).

        extra_headers: forwarded to the router (e.g. W3C `traceparent`).
        """
        if self.use_semaphore:
            async with _get_semaphore():
                return await self._complete_inner(messages, max_tokens, extra_headers)
        return await self._complete_inner(messages, max_tokens, extra_headers)

    async def _complete_inner(
        self,
        messages: list[dict],
        max_tokens: int,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, float]:
        t0 = time.perf_counter()
        resp = await _retry(
            lambda: self._raw.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                extra_headers=extra_headers or None,
            ),
            label=f"complete/{self.model}",
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        return resp.choices[0].message.content, latency_ms

    async def complete_structured(
        self,
        messages: list[dict],
        response_model: Type[T],
        max_tokens: int = 1024,
        extra_headers: dict[str, str] | None = None,
    ) -> T:
        """
        Structured completion using the router's native OpenAI Structured Outputs.

        Returns a validated instance of `response_model` (a Pydantic model).
        The router guarantees the response body validates against the schema via
        server-side grammar-constrained decoding (docs/router-contract.md §6.5),
        so this never needs to retry on malformed JSON.

        extra_headers: forwarded to the router (e.g. W3C `traceparent`).
        """
        if self.use_semaphore:
            async with _get_semaphore():
                return await self._complete_structured_inner(
                    messages, response_model, max_tokens, extra_headers)
        return await self._complete_structured_inner(
            messages, response_model, max_tokens, extra_headers)

    async def _complete_structured_inner(
        self,
        messages: list[dict],
        response_model: Type[T],
        max_tokens: int,
        extra_headers: dict[str, str] | None,
    ) -> T:
        schema = response_model.model_json_schema()
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": schema,
                "strict": True,
            },
        }

        resp = await _retry(
            lambda: self._raw.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                response_format=response_format,
                extra_headers=extra_headers or None,
            ),
            label=f"structured/{self.model}",
        )
        content = resp.choices[0].message.content or ""
        try:
            return response_model.model_validate_json(content)
        except ValidationError as exc:
            # The router commits to strict schema adherence; if we still get a
            # validation error, log the payload so the contract breach is visible.
            logger.error(
                "[structured/%s] response failed schema validation despite "
                "strict mode: %s\npayload=%s",
                self.model, exc, content[:500],
            )
            raise

    async def stream(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding token strings. Used for writing worker live preview."""
        # Streaming holds the semaphore slot for the entire generation.
        # Writing tasks are long — that's intentional (they need the full slot).
        if self.use_semaphore:
            async with _get_semaphore():
                async for token in self._stream_inner(messages, max_tokens):
                    yield token
        else:
            async for token in self._stream_inner(messages, max_tokens):
                yield token

    async def _stream_inner(
        self,
        messages: list[dict],
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        # Retry only the initial request (opening the stream). Once tokens
        # start flowing, a mid-stream error shouldn't restart from the top.
        stream = await _retry(
            lambda: self._raw.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
            ),
            label=f"stream/{self.model}",
        )
        async for chunk in stream:
            # vLLM (and some OpenAI-compat servers) emit a final chunk with
            # choices: [] as a stream-done marker — guard against IndexError.
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
