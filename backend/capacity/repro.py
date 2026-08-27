"""
Reproducibility metadata stamped into every capacity result: enough to rerun
the same benchmark and to explain a different number later. All best-effort —
a field that can't be read is None, never an error.
"""
from __future__ import annotations

import glob
import hashlib
import os
import platform
import subprocess

import httpx

from backend.capacity import scenarios as scen_mod


def scenario_fingerprint() -> str | None:
    """Hash of the scenario/tile file — catches ANY workload edit, versioned or not."""
    try:
        with open(scen_mod._PATH, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2,
                             cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__)))))
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def host_info() -> dict:
    mem_gb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_gb = round(int(line.split()[1]) / 1048576, 1)
                    break
    except OSError:
        pass
    cpu_model = None
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_model": cpu_model,
        "mem_total_gb": mem_gb,
        "numa_nodes": len(glob.glob("/sys/devices/system/node/node[0-9]*")) or None,
        # Orchestrator topology changes agent-host capacity as much as the
        # hardware does: 0 = single-process (GIL-capped at ~one core).
        "orchestrator_workers": int(os.getenv("ADL_WORKERS", "0") or 0),
        "python": platform.python_version(),
        # The record has to name the database that served the run: the same
        # code path runs on SQLite or Postgres, and the two do not produce
        # comparable numbers.
        "database": _database_dialect(),
        "requirements_sha": _file_sha("backend/requirements.txt"),
        "git_dirty": _git_dirty(),
        # The generator lives in the control plane. That contamination is
        # MEASURED, not assumed: cpu_breakdown carries the control group per
        # run (0.3% of the host at 21.5 wf/s), open-loop levels record the
        # generator's own CPU share, and every level records the ACHIEVED
        # arrival rate next to the offered one — a generator that falls
        # behind censors the run instead of misreporting it.
        "load_generator": ("in-process, batched 20ms ticks (control plane "
                           "drives launch_run directly); achieved arrival "
                           "rate recorded per level"),
    }


def _database_dialect() -> str | None:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return "sqlite (default)"
    scheme = url.split("://", 1)[0]
    return scheme or None


def _file_sha(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def _git_dirty() -> bool | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                             text=True, timeout=5)
        return bool(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


async def engine_info(base_url: str) -> dict | None:
    """SGLang /get_server_info subset: the flags that change a capacity number."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            r = await http.get(f"{root}/get_server_info")
        if r.status_code != 200:
            return None
        data = r.json()
        keys = ("model_path", "served_model_name", "quantization",
                "attention_backend", "max_running_requests", "context_length",
                "chunked_prefill_size", "max_total_tokens", "mem_fraction_static")
        return {k: data.get(k) for k in keys if data.get(k) is not None} or None
    except Exception:  # noqa: BLE001
        return None
