"""
Local LLM engine manager — status of the on-box SGLang server and a supervised
bring-up (deploy/ensure-local-llm.sh) whose progress lines stream to the UI.

The ensure script is idempotent: serving -> no-op; container loading -> wait;
model or image missing -> download/pull, then launch (the known-good Qwen3 FP8
Xeon config).
"""
from __future__ import annotations

import asyncio
import os
from collections import deque
from pathlib import Path

import httpx

from backend.capacity.client import LOCAL_BASE, LOCAL_MODEL

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "ensure-local-llm.sh"

_proc: asyncio.subprocess.Process | None = None
_log: deque[str] = deque(maxlen=60)
_state = "idle"  # idle | starting | ready | failed


async def probe() -> dict:
    """Is the local engine serving right now?"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.get(f"{LOCAL_BASE.rstrip('/')}/models")
        if r.status_code == 200:
            ids = [m.get("id") for m in (r.json().get("data") or [])]
            return {"serving": True, "models": ids}
    except Exception:  # noqa: BLE001
        pass
    return {"serving": False, "models": []}


def status() -> dict:
    running = _proc is not None and _proc.returncode is None
    return {
        "base_url": LOCAL_BASE,
        "model": LOCAL_MODEL,
        "setup_state": "starting" if running else _state,
        "setup_log": list(_log),
    }


async def start() -> dict:
    """Kick the ensure script (idempotent). Returns immediately; poll status()."""
    global _proc, _state
    if _proc is not None and _proc.returncode is None:
        return {"started": False, "reason": "already running"}
    if not _SCRIPT.exists():
        _state = "failed"
        _log.append(f"ensure script missing: {_SCRIPT}")
        return {"started": False, "reason": "script missing"}
    _log.clear()
    _state = "starting"
    _proc = await asyncio.create_subprocess_exec(
        "bash", str(_SCRIPT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ},
    )
    asyncio.get_event_loop().create_task(_pump())
    return {"started": True}


async def _pump():
    global _state
    assert _proc is not None
    while True:
        line = await _proc.stdout.readline()
        if not line:
            break
        _log.append(line.decode(errors="replace").rstrip()[:200])
    rc = await _proc.wait()
    _state = "ready" if rc == 0 else "failed"
    _log.append(f"[ensure-llm] exited rc={rc}")
