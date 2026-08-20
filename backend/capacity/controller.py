"""
The capacity test controller — the "speed test" state machine.

Ramp model: virtual users are added on a fixed cadence, each user being one of
the five fixed agent scenarios looping continuously. The ramp stops when the
system shows CONSISTENT saturation, then holds at that level to measure a clean
steady state, and reports a speed-test-style result.

Saturation verdicts (first one to fire wins):
  cpu       local mode: CPU sustained >= cpu_target for 2 consecutive intervals
  plateau   throughput gained < 5% across 2 consecutive user-adds (any mode)
  errors    error rate over an interval exceeded 10%
  capped    max_users reached (held and measured there)
  timeout   max_duration elapsed

One test at a time, per process. Results are kept in memory and written to
data/capacity/ as JSON for history.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from pathlib import Path

from backend.capacity.client import StepCaller
from backend.capacity.scenarios import load_scenarios
from backend.capacity.telemetry import SystemSampler

RESULTS_DIR = Path("data/capacity")

DEFAULTS = dict(
    start_users=1,
    step_users=1,          # users added per interval
    step_interval_s=12.0,  # ramp cadence
    hold_s=20.0,           # steady-state measure window after saturation
    max_users=64,
    max_duration_s=420.0,
    cpu_target=90.0,       # local-mode saturation line
    plateau_gain=0.05,     # <5% throughput gain across a step = no headroom
    error_rate_limit=0.10,
    sample_interval_s=2.0,
    mock_ms=2000.0,
    mock_sigma=300.0,
    remote_budget=500,     # hard request cap for remote_real
)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round(p / 100 * (len(values) - 1)))))
    return round(values[idx], 1)


class CapacityTest:
    def __init__(self, mode: str, scenario_ids: list[str], cfg: dict):
        self.mode = mode
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
        all_scen = load_scenarios()
        self.scenario_ids = [s for s in scenario_ids if s in all_scen] or list(all_scen)
        self.scenarios = {sid: all_scen[sid] for sid in self.scenario_ids}

        self.phase = "starting"        # starting | ramping | holding | done | stopped | error
        self.verdict: str | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.error: str | None = None

        self.users: list[asyncio.Task] = []
        self.user_scenario: list[str] = []
        self.calls: deque[dict] = deque(maxlen=100_000)   # every completed call
        self.samples: deque[dict] = deque(maxlen=1200)    # system telemetry
        self.total_requests = 0
        self.result: dict | None = None

        self._caller = StepCaller(mode, mock_ms=self.cfg["mock_ms"],
                                  mock_sigma=self.cfg["mock_sigma"])
        self._sampler = SystemSampler()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def run(self):
        try:
            self._tasks.append(asyncio.create_task(self._sample_loop()))
            await self._ramp()
        except Exception as exc:  # noqa: BLE001
            self.phase, self.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            self._stop.set()
            for t in [*self.users, *self._tasks]:
                t.cancel()
            await asyncio.gather(*self.users, *self._tasks, return_exceptions=True)
            await self._caller.aclose()
            self.ended_at = time.time()
            if self.phase not in ("error", "stopped"):
                self.phase = "done"
            self._finalize()

    def stop(self):
        self.phase = "stopped"
        self._stop.set()

    # ── virtual users ────────────────────────────────────────────────────────
    def _add_user(self):
        idx = len(self.users)
        sid = self.scenario_ids[idx % len(self.scenario_ids)]
        self.user_scenario.append(sid)
        self.users.append(asyncio.create_task(self._user_loop(idx, sid)))

    async def _user_loop(self, idx: int, sid: str):
        scenario = self.scenarios[sid]
        think = float(scenario.get("think_ms", 1000)) / 1000.0
        while not self._stop.is_set():
            for step in scenario["steps"]:
                if self._stop.is_set():
                    return
                if (self.mode == "remote_real"
                        and self.total_requests >= self.cfg["remote_budget"]):
                    self.stop()  # hard budget: never spray a cloud API
                    return
                self.total_requests += 1
                rec = await self._caller.call(scenario, step)
                rec.update(scenario=sid, step=step.get("label"), user=idx, ts=time.time())
                self.calls.append(rec)
            await asyncio.sleep(think)

    # ── telemetry ────────────────────────────────────────────────────────────
    async def _sample_loop(self):
        while not self._stop.is_set():
            s = self._sampler.sample()
            s["users"] = len(self.users)
            s.update(self._window_stats(self.cfg["sample_interval_s"] * 5))
            self.samples.append(s)
            await asyncio.sleep(self.cfg["sample_interval_s"])

    def _window_stats(self, window_s: float) -> dict:
        cut = time.time() - window_s
        recent = [c for c in self.calls if c["ts"] >= cut]
        ok = [c for c in recent if c["ok"]]
        lat = [c["latency_ms"] for c in ok]
        toks = sum(c["tokens_out"] for c in ok)
        return {
            "tps": round(toks / window_s, 1),
            "rpm": round(len(ok) * 60.0 / window_s, 1),
            "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95),
            "err_rate": round(1 - len(ok) / len(recent), 3) if recent else 0.0,
        }

    # ── the ramp ─────────────────────────────────────────────────────────────
    async def _ramp(self):
        for _ in range(int(self.cfg["start_users"])):
            self._add_user()
        self.phase = "ramping"
        interval = float(self.cfg["step_interval_s"])
        prev_tps: float | None = None
        cpu_hot = 0
        flat = 0

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return  # stopped externally
            except asyncio.TimeoutError:
                pass

            stats = self._window_stats(interval)
            cpus = [s["cpu_pct"] for s in self.samples
                    if s["cpu_pct"] is not None and s["ts"] >= time.time() - interval]
            avg_cpu = statistics.mean(cpus) if cpus else None
            elapsed = time.time() - self.started_at

            cpu_hot = cpu_hot + 1 if (
                self.mode == "local" and avg_cpu is not None
                and avg_cpu >= self.cfg["cpu_target"]) else 0
            if prev_tps is not None and prev_tps > 0:
                gain = (stats["tps"] - prev_tps) / prev_tps
                flat = flat + 1 if gain < self.cfg["plateau_gain"] else 0
            prev_tps = stats["tps"]

            if cpu_hot >= 2:
                self.verdict = "cpu"
            elif flat >= 2 and len(self.users) > int(self.cfg["start_users"]):
                self.verdict = "plateau"
            elif stats["err_rate"] > self.cfg["error_rate_limit"]:
                self.verdict = "errors"
            elif len(self.users) >= int(self.cfg["max_users"]):
                self.verdict = "capped"
            elif elapsed > float(self.cfg["max_duration_s"]):
                self.verdict = "timeout"

            if self.verdict:
                await self._hold()
                return

            for _ in range(int(self.cfg["step_users"])):
                if len(self.users) < int(self.cfg["max_users"]):
                    self._add_user()

    async def _hold(self):
        """Hold at the saturation level and measure a clean steady state."""
        self.phase = "holding"
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=float(self.cfg["hold_s"]))
        except asyncio.TimeoutError:
            pass

    # ── result ───────────────────────────────────────────────────────────────
    def _finalize(self):
        hold = self._window_stats(float(self.cfg["hold_s"]))
        cut = time.time() - float(self.cfg["hold_s"])
        hold_samples = [s for s in self.samples if s["ts"] >= cut]

        def avg(key):
            vals = [s[key] for s in hold_samples if s.get(key) is not None]
            return round(statistics.mean(vals), 1) if vals else None

        per_scenario: dict[str, dict] = {}
        for sid in self.scenario_ids:
            cs = [c for c in self.calls if c["scenario"] == sid]
            ok = [c for c in cs if c["ok"]]
            per_scenario[sid] = {
                "name": self.scenarios[sid]["name"],
                "users": self.user_scenario.count(sid),
                "calls": len(cs),
                "errors": len(cs) - len(ok),
                "p50_ms": _pct([c["latency_ms"] for c in ok], 50),
                "p95_ms": _pct([c["latency_ms"] for c in ok], 95),
                "tokens_out": sum(c["tokens_out"] for c in ok),
            }

        # Downsample the timeline for the result payload (~120 points max).
        samples = list(self.samples)
        stride = max(1, len(samples) // 120)
        timeline = samples[::stride]

        # Whole-test energy from average power over wall time (best-effort).
        powers = [s["power_w"] for s in samples if s.get("power_w") is not None]
        dur = (self.ended_at or time.time()) - self.started_at
        energy_wh = round(statistics.mean(powers) * dur / 3600, 2) if powers else None

        self.result = {
            "mode": self.mode,
            "verdict": self.verdict or ("stopped" if self.phase == "stopped" else None),
            "phase": self.phase,
            "error": self.error,
            "max_users": len(self.users),
            "duration_s": round(dur, 1),
            "total_requests": self.total_requests,
            "total_tokens_out": sum(c["tokens_out"] for c in self.calls if c["ok"]),
            "steady": {**hold, "cpu_pct": avg("cpu_pct"), "mem_pct": avg("mem_pct"),
                        "power_w": avg("power_w"), "load1": avg("load1")},
            "energy_wh": energy_wh,
            "per_scenario": per_scenario,
            "timeline": timeline,
            "config": {k: self.cfg[k] for k in
                       ("step_interval_s", "max_users", "cpu_target", "hold_s",
                        "mock_ms", "mock_sigma")},
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.started_at))
            (RESULTS_DIR / f"capacity-{stamp}-{self.mode}.json").write_text(
                json.dumps(self.result, indent=1))
        except OSError:
            pass  # results still available in memory

    # ── live status for the UI ───────────────────────────────────────────────
    def status(self) -> dict:
        latest = self.samples[-1] if self.samples else {}
        per_scenario = {}
        for sid in self.scenario_ids:
            cs = [c for c in self.calls if c["scenario"] == sid]
            ok = [c for c in cs if c["ok"]]
            per_scenario[sid] = {
                "name": self.scenarios[sid]["name"],
                "users": self.user_scenario.count(sid),
                "calls": len(cs),
                "errors": len(cs) - len(ok),
                "p50_ms": _pct([c["latency_ms"] for c in ok[-200:]], 50),
            }
        samples = list(self.samples)[-150:]
        return {
            "active": self.phase in ("starting", "ramping", "holding"),
            "phase": self.phase,
            "verdict": self.verdict,
            "mode": self.mode,
            "users": len(self.users),
            "elapsed_s": round(time.time() - self.started_at, 1),
            "total_requests": self.total_requests,
            "latest": latest,
            "per_scenario": per_scenario,
            "timeline": samples,
            "error": self.error,
            "result": self.result,
        }
