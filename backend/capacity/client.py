"""
The capacity test's LLM call layer — one async call per scenario step, in one of
three modes:

  local        the on-box SGLang engine (OpenAI-compatible, default :8000/v1)
  remote_mock  no network at all: latency drawn from a bell curve around a set
               point, tokens fabricated — for exercising the harness safely
  remote_real  a selected cloud model or custom OpenAI-compatible endpoint

Returns a uniform record: {ok, latency_ms, tokens_in, tokens_out, error?}.
"""
from __future__ import annotations

import asyncio
import os
import random
import time

import httpx

from backend.capacity.scenarios import build_prompt

LOCAL_BASE = os.getenv("CAPACITY_LOCAL_BASE_URL", "http://127.0.0.1:8000/v1")
LOCAL_MODEL = os.getenv("CAPACITY_LOCAL_MODEL", "qwen3_30b_a3b")
REMOTE_BASE = os.getenv("CAPACITY_REMOTE_BASE_URL", "")
REMOTE_KEY = os.getenv("CAPACITY_REMOTE_API_KEY", "")
REMOTE_MODEL = os.getenv("CAPACITY_REMOTE_MODEL", "")


def remote_real_configured() -> bool:
    return bool(REMOTE_BASE and REMOTE_MODEL)


class StepCaller:
    """Bound to a mode for the duration of one capacity test."""

    def __init__(self, mode: str, *, mock_ms: float = 2000.0, mock_sigma: float = 300.0,
                 cache_mode: str = "warm", http: httpx.AsyncClient | None = None,
                 endpoint: dict | None = None):
        self.mode = mode
        self.mock_ms = float(mock_ms)
        self.mock_sigma = float(mock_sigma)
        self.cache_mode = cache_mode if cache_mode in ("warm", "cold") else "warm"
        self.endpoint = endpoint
        self._langchain_model = None
        self._http = http  # injected in tests; else created per test run
        if mode == "remote_real" and endpoint is not None and http is None:
            from backend.inference.model import ModelFactory
            self._langchain_model = ModelFactory(
                base_url=endpoint["base_url"], api_key=endpoint.get("api_key", ""),
                model_override=endpoint["model"], provider=endpoint["provider"],
            ).auto()
        elif mode in ("local", "remote_real") and http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()

    async def call(self, scenario: dict, step: dict,
                   extra_context_tokens: int = 0, vary_key: str = "0") -> dict:
        t0 = time.perf_counter()
        try:
            if self.mode == "remote_mock":
                # Bell curve around the set point; clamp so the tail never goes
                # negative or silly-short. (Latency stays context-independent by
                # design — the compounding context shows up in the token/KV
                # telemetry; local/real modes slow down naturally.)
                delay = max(0.05, random.gauss(self.mock_ms, self.mock_sigma) / 1000.0)
                await asyncio.sleep(delay)
                return {
                    "ok": True,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": int(step.get("prompt_tokens", 0)) + int(extra_context_tokens),
                    "tokens_out": int(step.get("max_tokens", 0) * 0.8),
                }
            messages = build_prompt(step, scenario.get("name", "?"),
                                    extra_context_tokens, vary_key=vary_key,
                                    cache_mode=self.cache_mode)
            if self._langchain_model is not None:
                reply = await self._langchain_model.ainvoke(messages)
                usage = getattr(reply, "usage_metadata", None) or {}
                meta_usage = (getattr(reply, "response_metadata", None) or {}).get(
                    "token_usage", {})
                return {
                    "ok": True,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": int(usage.get("input_tokens")
                                     or meta_usage.get("prompt_tokens") or 0),
                    "tokens_out": int(usage.get("output_tokens")
                                      or meta_usage.get("completion_tokens") or 0),
                }
            base, model, headers = self._target()
            resp = await self._http.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": int(step.get("max_tokens", 200)),
                    "temperature": 0,
                },
            )
            latency = round((time.perf_counter() - t0) * 1000, 1)
            if resp.status_code != 200:
                return {"ok": False, "latency_ms": latency, "tokens_in": 0, "tokens_out": 0,
                        "error": f"HTTP {resp.status_code}: {resp.text[:120]}"}
            data = resp.json()
            usage = data.get("usage") or {}
            return {
                "ok": True,
                "latency_ms": latency,
                "tokens_in": int(usage.get("prompt_tokens") or 0),
                "tokens_out": int(usage.get("completion_tokens") or 0),
            }
        except Exception as exc:  # noqa: BLE001 — a failed call is a data point
            return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": 0, "tokens_out": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:160]}

    def _target(self) -> tuple[str, str, dict]:
        if self.mode == "local":
            return LOCAL_BASE.rstrip("/"), LOCAL_MODEL, {}
        if self.mode == "remote_real":
            if self.endpoint:
                headers = ({"Authorization": f"Bearer {self.endpoint['api_key']}"}
                           if self.endpoint.get("api_key") else {})
                return (self.endpoint["base_url"].rstrip("/"),
                        self.endpoint["model"], headers)
            if not remote_real_configured():
                raise RuntimeError("remote_real not configured "
                                   "(CAPACITY_REMOTE_BASE_URL / CAPACITY_REMOTE_MODEL)")
            headers = {"Authorization": f"Bearer {REMOTE_KEY}"} if REMOTE_KEY else {}
            return REMOTE_BASE.rstrip("/"), REMOTE_MODEL, headers
        raise RuntimeError(f"unknown mode {self.mode!r}")
