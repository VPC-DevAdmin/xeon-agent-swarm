"""
System telemetry for the capacity test — stdlib-only readers over /proc and RAPL.

Everything is best-effort: on a box without a given source (RAPL is often
root-only; macOS dev machines have no /proc) the reading is simply None and the
UI shows a dash. Pure parse functions are separated from I/O so they unit-test
offline with canned file contents.
"""
from __future__ import annotations

import glob
import os
import time


# ── pure parsers (unit-testable) ─────────────────────────────────────────────

def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """First 'cpu ' line -> (busy_jiffies, total_jiffies)."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:]]
            if len(parts) < 5:
                return None
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
            total = sum(parts)
            return total - idle, total
    return None


def cpu_pct_from(prev: tuple[int, int], cur: tuple[int, int]) -> float | None:
    dbusy, dtotal = cur[0] - prev[0], cur[1] - prev[1]
    if dtotal <= 0:
        return None
    return round(100.0 * dbusy / dtotal, 1)


def parse_meminfo(text: str) -> tuple[float, float] | None:
    """-> (used_gb, used_pct) from MemTotal/MemAvailable."""
    vals: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith(("MemTotal:", "MemAvailable:")):
            vals[line.split(":")[0]] = int(line.split()[1])  # kB
    if "MemTotal" not in vals or "MemAvailable" not in vals or not vals["MemTotal"]:
        return None
    used_kb = vals["MemTotal"] - vals["MemAvailable"]
    return round(used_kb / 1048576, 1), round(100.0 * used_kb / vals["MemTotal"], 1)


# ── samplers (I/O) ───────────────────────────────────────────────────────────

class SystemSampler:
    """Call sample() periodically; returns a dict of best-effort readings.

    CPU% and power are deltas, so the first sample returns None for both.
    """

    def __init__(self):
        self._prev_stat: tuple[int, int] | None = None
        self._prev_energy: tuple[float, float] | None = None  # (uj_total, ts)
        self._rapl_paths = sorted(glob.glob("/sys/class/powercap/intel-rapl:?/energy_uj"))

    def _read_energy_uj(self) -> float | None:
        if not self._rapl_paths:
            return None
        total = 0.0
        try:
            for p in self._rapl_paths:
                with open(p) as f:
                    total += float(f.read().strip())
            return total
        except OSError:
            return None  # typically EACCES for non-root

    def sample(self) -> dict:
        now = time.time()
        out: dict = {"ts": now, "cpu_pct": None, "mem_gb": None, "mem_pct": None,
                     "load1": None, "power_w": None}
        try:
            out["load1"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        try:
            with open("/proc/stat") as f:
                cur = parse_proc_stat(f.read())
            if cur:
                if self._prev_stat:
                    out["cpu_pct"] = cpu_pct_from(self._prev_stat, cur)
                self._prev_stat = cur
        except OSError:
            pass
        try:
            with open("/proc/meminfo") as f:
                mem = parse_meminfo(f.read())
            if mem:
                out["mem_gb"], out["mem_pct"] = mem
        except OSError:
            pass
        uj = self._read_energy_uj()
        if uj is not None:
            if self._prev_energy:
                prev_uj, prev_ts = self._prev_energy
                duj, dt = uj - prev_uj, now - prev_ts
                if dt > 0 and duj >= 0:  # counter can wrap; skip that sample
                    out["power_w"] = round(duj / dt / 1_000_000, 1)
            self._prev_energy = (uj, now)
        return out


# ── memory bandwidth (perf uncore IMC) + engine KV utilization ────────────────
# Both best-effort with pure parsers, same as above. Bandwidth needs `perf` and
# uncore access (kernel.perf_event_paranoid <= 0 or CAP_PERFMON); KV utilization
# needs the SGLang engine launched with --enable-metrics (ensure-local-llm.sh
# does). Absent either, the reading is None and the UI shows a dash.

def parse_perf_cas_mib(text: str) -> float | None:
    """Sum MiB from `perf stat -x,` CSV lines for cas_count_read/write.

    Line shape: value,unit,event,run_time,pct,... — value may be '<not supported>'.
    """
    total = 0.0
    seen = False
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 3 or "cas_count" not in parts[2]:
            continue
        try:
            total += float(parts[0])
            seen = True
        except ValueError:
            continue  # <not counted> / <not supported>
    return total if seen else None


def parse_sglang_token_usage(text: str) -> float | None:
    """Extract SGLang's KV-pool utilization (0..1) from its /metrics page -> pct."""
    import re
    m = re.search(r"sglang:token_usage(?:\{[^}]*\})?\s+([0-9.eE+-]+)", text)
    if not m:
        return None
    try:
        return round(float(m.group(1)) * 100.0, 1)
    except ValueError:
        return None


def mem_slope_mb_per_user(samples: list[dict]) -> float | None:
    """Least-squares slope of memory (MB) vs virtual users over the ramp — the
    measured marginal memory cost of one more agent."""
    pts = [(s["users"], s["mem_gb"] * 1024) for s in samples
           if s.get("mem_gb") is not None and s.get("users") is not None]
    if len({u for u, _ in pts}) < 3:
        return None
    n = len(pts)
    sx = sum(u for u, _ in pts); sy = sum(m for _, m in pts)
    sxx = sum(u * u for u, _ in pts); sxy = sum(u * m for u, m in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return round((n * sxy - sx * sy) / denom, 1)


async def sample_bandwidth_gbs(duration: float = 1.0) -> float | None:
    """Measure DRAM bandwidth over `duration` seconds via uncore IMC CAS counts."""
    import asyncio
    try:
        proc = await asyncio.create_subprocess_exec(
            "perf", "stat", "-a", "-x,",
            "-e", "uncore_imc/cas_count_read/,uncore_imc/cas_count_write/",
            "sleep", str(duration),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=duration + 5)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return None
    mib = parse_perf_cas_mib(err.decode(errors="replace"))
    if mib is None:
        return None
    return round(mib / duration / 1024, 1)


async def sample_kv_pct(base_url: str) -> float | None:
    """Scrape the local engine's KV utilization from its Prometheus /metrics."""
    import httpx
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            r = await http.get(f"{root}/metrics")
        if r.status_code != 200:
            return None
        return parse_sglang_token_usage(r.text)
    except Exception:  # noqa: BLE001
        return None
