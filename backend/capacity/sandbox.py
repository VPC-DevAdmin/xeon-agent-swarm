"""Sandboxed tool execution for the capacity workload (v17).

A worker that "runs a data job" does it here: a fresh, isolated Python
interpreter per job, with CPU-time, address-space, and wall-clock limits,
single-threaded math (OpenBLAS would otherwise fan out to every core),
and no network when the host allows a network namespace. The job is
seeded and deterministic: it generates its own dataset from the seed,
aggregates it, and returns a few hundred characters of results that go
back into the worker's context. Two declared sizes:

    light  ~0.25 core-seconds  (450k rows)      the comparison's analysis step
    heavy  ~2 core-seconds     (3.3M rows)        each data-analyst worker

The cost is real CPU on the host, bounded by the size in the contract,
and the isolation mode is part of the machine fingerprint:

    netns    sudo unshare -n, then drop to the invoking user; no network
    rlimits  limits only (user namespaces unavailable and no sudo)

Why not a container per call: a warm code-interpreter pool is what
production agents use, and a fresh interpreter with limits models its
per-job cost honestly; a container launch per call would model a system
nobody deploys for this.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

JOB_SCRIPT = Path(__file__).with_name("sandbox_job.py")
# Rows per job, calibrated on the reference Xeon (one core, 3.6 GHz):
# light ~0.25 core-seconds, heavy ~2 core-seconds, interpreter start and
# numpy import included. The job reads this from argv - one source.
SIZES = {"light": 450_000, "heavy": 3_300_000}
CPU_LIMIT_S = int(os.getenv("CAPACITY_SANDBOX_CPU_S", "30") or 30)
# Address-space limit, not resident: numpy + OpenBLAS reserve several GB of
# virtual space at import even single-threaded, so the cap is 8 GB while a
# heavy job's resident set is ~0.5 GB.
MEM_LIMIT_BYTES = int(os.getenv("CAPACITY_SANDBOX_MEM_MB", "8192") or 8192) * 1024 * 1024
WALL_LIMIT_S = float(os.getenv("CAPACITY_SANDBOX_WALL_S", "60") or 60)

_mode: str | None = None


def isolation_mode() -> str:
    """Decided once per process: netns when sudo -n and unshare work."""
    global _mode
    if _mode is None:
        forced = os.getenv("CAPACITY_SANDBOX_ISOLATION")
        if forced:
            _mode = forced
        elif (shutil.which("sudo") and shutil.which("unshare")
              and os.system("sudo -n unshare -n true >/dev/null 2>&1") == 0):
            _mode = "netns"
        else:
            _mode = "rlimits"
    return _mode


def _command(size: str, seed: int) -> list[str]:
    # The job interpreter is isolated (-I -S): hand it the one site dir that
    # holds numpy, found from the parent's own import, nothing else.
    try:
        import numpy
        site = os.path.dirname(os.path.dirname(numpy.__file__))
    except ImportError:
        site = next((p for p in sys.path if p.endswith("site-packages")), "")
    inner = [sys.executable, "-I", "-S", str(JOB_SCRIPT), size, str(seed), site,
             str(SIZES[size])]
    limits = ["prlimit", f"--cpu={CPU_LIMIT_S}", f"--as={MEM_LIMIT_BYTES}",
              "--fsize=1048576", "--nproc=64"] if shutil.which("prlimit") else []
    if isolation_mode() == "netns":
        # Drop back to the invoking user by uid, not $USER: executors run
        # without USER in their environment, and a job run as "nobody"
        # cannot load numpy's extensions from the user's venv.
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        return ["sudo", "-n", "unshare", "-n", "--", "sudo", "-n", "-u", user,
                *limits, *inner]
    return [*limits, *inner]


async def run_job(size: str, seed: int) -> dict:
    """Run one sandboxed job. Returns {ok, size, rows, elapsed_ms, cpu_ms,
    result} or {ok: False, error}."""
    if size not in SIZES:
        raise ValueError(f"unknown job size {size!r}")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0"}
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *_command(size, seed), env=env, cwd="/tmp",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=WALL_LIMIT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "size": size, "error": "wall limit",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
    elapsed = (time.perf_counter() - t0) * 1000
    if proc.returncode != 0:
        return {"ok": False, "size": size, "error": (err or b"")[-300:].decode(errors="replace"),
                "elapsed_ms": round(elapsed, 1)}
    try:
        payload = json.loads(out.decode())
    except ValueError:
        return {"ok": False, "size": size, "error": "malformed job output",
                "elapsed_ms": round(elapsed, 1)}
    payload.update(ok=True, size=size, elapsed_ms=round(elapsed, 1),
                   isolation=isolation_mode())
    # Stage timings ride the executor's retrieval stats flush, so a plateau
    # can be read for sandbox cost (wall, compute, CPU) per size.
    try:
        from backend.capacity import retrieval as _rt
        _rt._ensure_stats()
        _rt._note(f"sandbox_{size}_wall_ms", elapsed)
        _rt._note(f"sandbox_{size}_cpu_ms", float(payload.get("cpu_ms") or 0.0))
    except Exception:  # noqa: BLE001 - diagnostics never fail a job
        pass
    return payload
