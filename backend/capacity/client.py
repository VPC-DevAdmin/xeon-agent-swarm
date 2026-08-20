"""
The capacity test's LLM call layer — one async call per scenario step, in one of
three modes:

  local        the on-box SGLang engine (OpenAI-compatible, default :8000/v1)
  remote_mock  no network at all: latency drawn from a bell curve around a set
               point, tokens fabricated — for exercising the harness safely
  remote_real  a real cloud endpoint (OpenAI-compatible) from env config; the
               controller enforces a hard request budget on this mode

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
                 http: httpx.AsyncClient | None = None):
        self.mode = mode
        self.mock_ms = float(mock_ms)
        self.mock_sigma = float(mock_sigma)
        self._http = http  # injected in tests; else created per test run
        if mode in ("local", "remote_real") and http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()

    async def call(self, scenario: dict, step: dict) -> dict:
        t0 = time.perf_counter()
        try:
            if self.mode == "remote_mock":
                # Bell curve around the set point; clamp so the tail never goes
                # negative or silly-short.
                delay = max(0.05, random.gauss(self.mock_ms, self.mock_sigma) / 1000.0)
                await asyncio.sleep(delay)
                return {
                    "ok": True,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": int(step.get("prompt_tokens", 0)),
                    "tokens_out": int(step.get("max_tokens", 0) * 0.8),
                }
            base, model, headers = self._target()
            resp = await self._http.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": build_prompt(step, scenario.get("name", "?")),
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
            if not remote_real_configured():
                raise RuntimeError("remote_real not configured "
                                   "(CAPACITY_REMOTE_BASE_URL / CAPACITY_REMOTE_MODEL)")
            headers = {"Authorization": f"Bearer {REMOTE_KEY}"} if REMOTE_KEY else {}
            return REMOTE_BASE.rstrip("/"), REMOTE_MODEL, headers
        raise RuntimeError(f"unknown mode {self.mode!r}")
