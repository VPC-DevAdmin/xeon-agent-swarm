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
SIZES = {"light": 450_000, "heavy": 3_300_000, "xl": 30_000_000}
# Job KINDS beyond the data job (CPU-heavy mix, see docs/plan-cpu-heavy-mix.md):
#   build   generate a C project, compile it with gcc -O2, run its property
#           tests (the shape of a code agent's build-and-test step)
#   ops     repair a git repository with conflicting branches, then configure,
#           start and smoke-test a small service (the shape of the lab's
#           install-configure-verify tasks; mostly waiting, little compute)
#   ingest  parse a set of PDF pages, normalize and chunk the text (the
#           embedding and indexing happen on the executor, see toolbox)
# Each kind has its own script; per-kind limits below.
KINDS = ("light", "heavy", "xl", "build", "ops", "ingest")
KIND_SCRIPTS = {"build": Path(__file__).with_name("sandbox_build_job.py"),
                "ops": Path(__file__).with_name("sandbox_ops_job.py"),
                "ingest": Path(__file__).with_name("sandbox_ingest_job.py")}
BUILD_WORK = int(os.getenv("CAPACITY_BUILD_WORK", "3000000") or 3000000)   # inputs per property test (~4 s of tests on the reference Xeon)
INGEST_PAGES = int(os.getenv("CAPACITY_INGEST_PAGES", "200") or 200)
INGEST_DOCS = os.getenv("CAPACITY_INGEST_DOCS", "data/capacity/ingest")
CPU_LIMIT_S = int(os.getenv("CAPACITY_SANDBOX_CPU_S", "30") or 30)
# Heavier kinds get proportionate limits; a limit is a runaway guard, never a
# budget the job is expected to approach.
KIND_LIMITS = {"xl": (150, 300), "build": (150, 300), "ops": (60, 180), "ingest": (150, 300)}
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


def _site_dir() -> str:
    try:
        import numpy
        return os.path.dirname(os.path.dirname(numpy.__file__))
    except ImportError:
        return next((p for p in sys.path if p.endswith("site-packages")), "")


def _command(kind: str, seed: int) -> list[str]:
    # The job interpreter is isolated (-I -S): hand it the one site dir that
    # holds numpy (and pypdf), found from the parent's own import, nothing else.
    site = _site_dir()
    # Thread caps travel INSIDE the command: sudo resets the environment, and
    # without them OpenBLAS spawns a thread per CPU at import (128 here) and
    # numpy fails to load under the process limit.
    env_part = ["env", "OPENBLAS_NUM_THREADS=1", "OMP_NUM_THREADS=1",
                "MKL_NUM_THREADS=1", "PYTHONHASHSEED=0", "HOME=/tmp",
                "GIT_CONFIG_NOSYSTEM=1"]
    if kind in SIZES:
        script = [str(JOB_SCRIPT), kind, str(seed), site, str(SIZES[kind])]
    elif kind == "build":
        script = [str(KIND_SCRIPTS[kind]), str(seed), str(BUILD_WORK)]
    elif kind == "ops":
        script = [str(KIND_SCRIPTS[kind]), str(seed)]
    elif kind == "ingest":
        script = [str(KIND_SCRIPTS[kind]), str(seed), site,
                  str(Path(INGEST_DOCS).resolve()), str(INGEST_PAGES)]
    else:
        raise ValueError(f"unknown job kind {kind!r}")
    inner = [*env_part, sys.executable, "-I", "-S", *script]
    cpu_s, _wall = KIND_LIMITS.get(kind, (CPU_LIMIT_S, WALL_LIMIT_S))
    limits = ["prlimit", f"--cpu={cpu_s}", f"--as={MEM_LIMIT_BYTES}",
              "--fsize=1048576"] if shutil.which("prlimit") else []
    if isolation_mode() == "netns":
        # Drop back to the invoking user by uid, not $USER: executors run
        # without USER in their environment, and a job run as "nobody"
        # cannot load numpy's extensions from the user's venv.
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        if kind == "ops":
            # The ops job starts a service on the loopback INSIDE the fresh
            # network namespace, where lo is down until root raises it.
            import shlex
            tail = shlex.join(["sudo", "-n", "-u", user, *limits, *inner])
            return ["sudo", "-n", "unshare", "-n", "--", "sh", "-c",
                    "ip link set lo up 2>/dev/null; exec " + tail]
        return ["sudo", "-n", "unshare", "-n", "--", "sudo", "-n", "-u", user,
                *limits, *inner]
    return [*limits, *inner]


def wall_limit(kind: str) -> float:
    return float(KIND_LIMITS.get(kind, (0, WALL_LIMIT_S))[1])


async def run_job(size: str, seed: int) -> dict:
    """Run one sandboxed job of a kind (a data size, or build/ops/ingest).
    Returns {ok, size, elapsed_ms, cpu_ms, ...result} or {ok: False, error}."""
    if size not in KINDS:
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
        out, err = await asyncio.wait_for(proc.communicate(), timeout=wall_limit(size))
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
