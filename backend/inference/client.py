from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncGenerator

import httpx
import instructor
from openai import AsyncOpenAI

# Hard wall on any single inference call.
# Reasoning:
#   - fact_check (400 tok)   @ ~8 tok/s CPU  → ~50s  ← safe under 300s
#   - research/analysis      @ ~8 tok/s CPU  → ~150s ← safe
#   - writing (2000 tok)     @ ~8 tok/s CPU  → ~250s ← needs headroom
# 300s is a realistic ceiling for any role given their capped token budgets.
_INFERENCE_TIMEOUT = httpx.Timeout(timeout=300.0, connect=10.0)

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
    Wraps an OpenAI-compatible endpoint.
    Use `instructor.patch()` when structured output is needed (orchestrator).

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
        self._raw = AsyncOpenAI(
            base_url=base_url, api_key="none", timeout=_INFERENCE_TIMEOUT
        )
        self._instructor = instructor.from_openai(self._raw)
        self.model = model
        self.hardware = hardware
        self.use_semaphore = use_semaphore

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
    ) -> tuple[str, float]:
        """Plain completion. Returns (content, latency_ms)."""
        if self.use_semaphore:
            async with _get_semaphore():
                return await self._complete_inner(messages, max_tokens)
        return await self._complete_inner(messages, max_tokens)

    async def _complete_inner(
        self,
        messages: list[dict],
        max_tokens: int,
    ) -> tuple[str, float]:
        t0 = time.perf_counter()
        resp = await self._raw.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        return resp.choices[0].message.content, latency_ms

    async def complete_structured(
        self,
        messages: list[dict],
        response_model,
        max_tokens: int = 1024,
    ):
        """
        Structured completion via instructor. Returns a validated Pydantic model.
        Used by the orchestrator and validator.
        """
        if self.use_semaphore:
            async with _get_semaphore():
                return await self._complete_structured_inner(messages, response_model, max_tokens)
        return await self._complete_structured_inner(messages, response_model, max_tokens)

    async def _complete_structured_inner(
        self,
        messages: list[dict],
        response_model,
        max_tokens: int,
    ):
        return await self._instructor.chat.completions.create(
            model=self.model,
            messages=messages,
            response_model=response_model,
            max_tokens=max_tokens,
        )

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
        stream = await self._raw.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            # vLLM (and some OpenAI-compat servers) emit a final chunk with
            # choices: [] as a stream-done marker — guard against IndexError.
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
