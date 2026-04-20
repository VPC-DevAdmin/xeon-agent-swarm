import time
from openai import AsyncOpenAI
from typing import AsyncGenerator
import httpx
import instructor

# Hard wall on any single inference call.
# Reasoning:
#   - fact_check (400 tok)   @ ~8 tok/s CPU  → ~50s  ← safe under 300s
#   - research/analysis      @ ~8 tok/s CPU  → ~150s ← safe
#   - writing (2000 tok)     @ ~8 tok/s CPU  → ~250s ← needs headroom
# 90s was too tight for the writing worker (3000 tok → 375s worst-case).
# 300s is a realistic ceiling for any role given their capped token budgets.
_INFERENCE_TIMEOUT = httpx.Timeout(timeout=300.0, connect=10.0)


class InferenceClient:
    """
    Wraps an OpenAI-compatible endpoint.
    Use `instructor.patch()` when structured output is needed (orchestrator).
    """

    def __init__(self, base_url: str, model: str, hardware: str = "cpu"):
        self._raw = AsyncOpenAI(
            base_url=base_url, api_key="none", timeout=_INFERENCE_TIMEOUT
        )
        self._instructor = instructor.from_openai(self._raw)
        self.model = model
        self.hardware = hardware

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
    ) -> tuple[str, float]:
        """Plain completion. Returns (content, latency_ms)."""
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
        Used by the orchestrator to get a guaranteed TaskGraph.
        """
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
        """Async generator yielding token strings. Used for the A/B single-model panel."""
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
