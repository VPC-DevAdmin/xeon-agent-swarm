"""
The capacity test controller — the "speed test" state machine.

Ramp model: virtual users are added on a fixed cadence, each user being one of
the five fixed agent scenarios looping continuously. The ramp stops when the
system shows CONSISTENT saturation, then holds at that level to measure a clean
steady state, and reports a speed-test-style result.

CAPACITY DEFINITION (the number this test reports): the highest agent count at
which the service level still held — p95 latency within slo_p95_x × the
low-load baseline (or an absolute slo_p95_ms) AND error rate within slo_err.
When the ramp breaches the SLO it SCALES BACK DOWN to the last good level and
measures the steady state THERE: capacity is what you can sustain, not the
level you died at. Resource verdicts explain the cause.

Stop conditions (first one to fire wins):
  slo       p95 exceeded the SLO (or errors did) for 2 consecutive intervals —
            scale down to the last good level, hold, measure
  cpu       local mode: CPU sustained >= cpu_target for 2 consecutive intervals
  memory    local mode: system memory sustained >= mem_target — RAM can gate
            before cores do on big-model boxes
  kv        local mode: the engine's KV-cache pool sustained >= kv_target
            (scraped from SGLang /metrics; the truest "model memory full")
  plateau   throughput gain fell below plateau_frac × the EXPECTED linear gain
            (step_users/users) for 2 consecutive adds — relative, so it means
            diminishing returns at any scale (a fixed % threshold falsely fires
            once 1/N drops under it)
  errors    error rate over an interval exceeded error_rate_limit (hard stop)
  capped    max_users reached (held and measured there)
  timeout   max_duration elapsed

One test at a time, per process. Results are kept in memory and written to
data/capacity/ as JSON for history.
"""
from __future__ import annotations

import asyncio
import json
import random
import statistics
import time
from collections import deque
from pathlib import Path

from backend.capacity.client import StepCaller
from backend.capacity.scenarios import load_scenarios
from backend.capacity.telemetry import (SystemSampler, mem_slope_mb_per_user,
                                         sample_bandwidth_gbs, sample_kv_pct)
from backend.capacity.client import LOCAL_BASE

RESULTS_DIR = Path("data/capacity")

DEFAULTS = dict(
    start_users=1,
    step_users=1,          # users added per interval
    step_interval_s=12.0,  # ramp cadence
    hold_s=20.0,           # steady-state measure window after saturation
    max_users=64,
    max_duration_s=420.0,
    cpu_target=90.0,       # local-mode saturation line
    mem_target=92.0,       # local-mode RAM saturation line (%)
    kv_target=90.0,        # local-mode KV-pool saturation line (%)
    slo_p95_x=3.0,         # SLO: p95 may grow to this multiple of the baseline
    slo_p95_ms=None,       # absolute p95 SLO in ms (overrides the multiplier)
    slo_err=0.05,          # SLO: max error rate while a level counts as "good"
    plateau_frac=0.25,     # gain < 25% of the expected linear gain, twice = knee
    error_rate_limit=0.10, # hard stop

    sample_interval_s=2.0,
    mock_ms=2000.0,
    mock_sigma=300.0,
    remote_budget=500,     # hard request cap for remote_real
)


async def run_scenario_loop(call, scenario: dict, sid: str, idx: int,
                            session_tokens: int, stop=None) -> int:
    """Execute ONE turn of an agent scenario; returns the context tokens the
    session carries into the next turn (or -1 if stopped mid-loop).

    call(scenario, step, sid, idx, extra_tokens, label) -> rec|None performs one
    recorded LLM request. Semantics per step:
      - base prompt + (carried context when carry_context) + session history
      - each tool round-trip: wait tool_latency_ms (the agent HOLDS its context
        while the external tool runs), inject tool_result_tokens, call again
    """
    cap = int(scenario.get("context_cap", 6000))
    context = min(int(session_tokens), cap)
    for step in scenario.get("steps", []):
        carried = context if step.get("carry_context") else min(int(session_tokens), cap)
        rec = await call(scenario, step, sid, idx, min(carried, cap),
                         step.get("label", "step"))
        if rec is None:
            return -1
        context = min(cap, context + int(rec.get("tokens_out") or 0))
        for i in range(int(step.get("tool_calls", 0))):
            lat = max(0.02, random.gauss(float(step.get("tool_latency_ms", 300)),
                                         float(step.get("tool_latency_ms", 300)) * 0.25) / 1000.0)
            if stop is not None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=lat)
                    return -1           # stopped while "waiting on the tool"
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(lat)
            carried = min(cap, carried + int(step.get("tool_result_tokens", 400))
                          + int(rec.get("tokens_out") or 0))
            rec = await call(scenario, step, sid, idx, carried,
                             f"{step.get('label', 'step')}+tool{i + 1}")
            if rec is None:
                return -1
            # The tool result is now part of the conversation: later carry
            # steps and the session must see it, not just this continuation.
            context = min(cap, context + int(step.get("tool_result_tokens", 400))
                          + int(rec.get("tokens_out") or 0))
    return context


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
        self.baseline_p95: float | None = None   # low-load p95, the SLO reference
        self.capacity_users: int | None = None   # last level where the SLO held
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

    def _remove_users(self, n: int):
        """Scale back down (SLO breach): capacity is measured at a level that
        WORKS, so the breached level's users are cancelled before the hold."""
        for _ in range(min(n, len(self.users) - 1)):
            task = self.users.pop()
            self.user_scenario.pop()
            task.cancel()

    async def _user_loop(self, idx: int, sid: str):
        """One virtual user: an agent SESSION, not a chatbot pinger.

        Context compounds within a turn (carry_context steps read everything
        produced so far, tool results get injected mid-step) and ACROSS turns
        for session_turns loops before the session resets — the growing-prefill,
        growing-KV signature that separates agents from flat chat traffic."""
        scenario = self.scenarios[sid]
        think = float(scenario.get("think_ms", 1000)) / 1000.0
        session_tokens = 0
        turn = 0
        while not self._stop.is_set():
            session_tokens = await run_scenario_loop(
                self._record_call, scenario, sid, idx, session_tokens,
                stop=self._stop)
            if session_tokens < 0:      # stopped mid-loop
                return
            turn += 1
            if turn % int(scenario.get("session_turns", 1)) == 0:
                session_tokens = 0      # session over — context window cleared
            await asyncio.sleep(think)

    async def _record_call(self, scenario, step, sid, idx, extra_tokens, label):
        """Budget-checked, recorded single LLM call (incl. tool continuations)."""
        if self._stop.is_set():
            return None
        if (self.mode == "remote_real"
                and self.total_requests >= self.cfg["remote_budget"]):
            self.stop()  # hard budget: never spray a cloud API
            return None
        self.total_requests += 1
        rec = await self._caller.call(scenario, step, extra_context_tokens=extra_tokens)
        rec.update(scenario=sid, step=label, user=idx, ts=time.time())
        self.calls.append(rec)
        return rec

    # ── telemetry ────────────────────────────────────────────────────────────
    async def _sample_loop(self):
        # Bandwidth/KV are local-mode readings; stop attempting after repeated
        # misses so we never spawn perf / scrape a dead endpoint in a tight loop.
        bw_misses = 0
        kv_misses = 0
        while not self._stop.is_set():
            s = self._sampler.sample()
            s["users"] = len(self.users)
            s.update(self._window_stats(self.cfg["sample_interval_s"] * 5))
            s["bw_gbs"] = None
            s["kv_pct"] = None
            if self.mode == "local":
                if bw_misses < 3:
                    s["bw_gbs"] = await sample_bandwidth_gbs(
                        max(0.5, self.cfg["sample_interval_s"] - 1.0))
                    bw_misses = 0 if s["bw_gbs"] is not None else bw_misses + 1
                if kv_misses < 3:
                    s["kv_pct"] = await sample_kv_pct(LOCAL_BASE)
                    kv_misses = 0 if s["kv_pct"] is not None else kv_misses + 1
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
        prev_users = int(self.cfg["start_users"])
        cpu_hot = 0
        mem_hot = 0
        kv_hot = 0
        flat = 0
        slo_bad = 0

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return  # stopped externally
            except asyncio.TimeoutError:
                pass

            stats = self._window_stats(interval)
            cut = time.time() - interval
            def _avg(key):
                vals = [s[key] for s in self.samples
                        if s.get(key) is not None and s["ts"] >= cut]
                return statistics.mean(vals) if vals else None
            avg_cpu, avg_mem, avg_kv = _avg("cpu_pct"), _avg("mem_pct"), _avg("kv_pct")
            elapsed = time.time() - self.started_at

            # SLO evaluation: the baseline is the first interval's p95 at the
            # starting load — the reference for "how the service behaves when
            # healthy". A level is GOOD when p95 and errors are within the SLO.
            if self.baseline_p95 is None and stats["p95_ms"] is not None:
                self.baseline_p95 = stats["p95_ms"]
            slo_ms = (self.cfg["slo_p95_ms"]
                      or (self.baseline_p95 * self.cfg["slo_p95_x"]
                          if self.baseline_p95 else None))
            good = ((slo_ms is None or stats["p95_ms"] is None
                     or stats["p95_ms"] <= slo_ms)
                    and stats["err_rate"] <= self.cfg["slo_err"])
            if good:
                self.capacity_users = len(self.users)
                slo_bad = 0
            else:
                slo_bad = slo_bad + 1

            local = self.mode == "local"
            cpu_hot = cpu_hot + 1 if (
                local and avg_cpu is not None
                and avg_cpu >= self.cfg["cpu_target"]) else 0
            mem_hot = mem_hot + 1 if (
                local and avg_mem is not None
                and avg_mem >= self.cfg["mem_target"]) else 0
            kv_hot = kv_hot + 1 if (
                local and avg_kv is not None
                and avg_kv >= self.cfg["kv_target"]) else 0
            # Relative plateau: compare the measured gain against the gain
            # PERFECT scaling would have produced for the users just added
            # (step/users). A fixed % threshold would falsely fire once 1/N
            # drops below it, reporting arithmetic instead of capacity.
            frac = float(self.cfg["plateau_frac"] or 0)
            if prev_tps is not None and prev_tps > 0 and frac > 0:
                gain = (stats["tps"] - prev_tps) / prev_tps
                added = max(0, len(self.users) - prev_users)
                expected = added / max(1, prev_users)
                flat = flat + 1 if (added > 0 and gain < frac * expected) else 0
            prev_tps = stats["tps"]
            prev_users = len(self.users)

            if slo_bad >= 2:
                self.verdict = "slo"
            elif cpu_hot >= 2:
                self.verdict = "cpu"
            elif mem_hot >= 2:
                self.verdict = "memory"
            elif kv_hot >= 2:
                self.verdict = "kv"
            elif flat >= 2 and len(self.users) > int(self.cfg["start_users"]):
                self.verdict = "plateau"
            elif stats["err_rate"] > self.cfg["error_rate_limit"]:
                self.verdict = "errors"
            elif len(self.users) >= int(self.cfg["max_users"]):
                self.verdict = "capped"
            elif elapsed > float(self.cfg["max_duration_s"]):
                self.verdict = "timeout"

            if self.verdict:
                if (self.verdict == "slo" and self.capacity_users
                        and self.capacity_users < len(self.users)):
                    # Measure capacity at the last level that MET the SLO.
                    self._remove_users(len(self.users) - self.capacity_users)
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
            dur_so_far = max(1e-6, (self.ended_at or time.time()) - self.started_at)
            # ESTIMATE: average tokens concurrently in flight for this profile
            # (token-seconds per second over request lifetimes). This approximates
            # KV pressure during active requests only — whether the engine retains
            # KV/prefix state between requests is engine policy; the measured value
            # is the SGLang KV gauge (kv_pct).
            kv_tok = sum((c["tokens_in"] + c["tokens_out"]) * c["latency_ms"] / 1000.0
                         for c in ok) / dur_so_far
            per_scenario[sid] = {
                "name": self.scenarios[sid]["name"],
                "users": self.user_scenario.count(sid),
                "calls": len(cs),
                "errors": len(cs) - len(ok),
                "p50_ms": _pct([c["latency_ms"] for c in ok], 50),
                "p95_ms": _pct([c["latency_ms"] for c in ok], 95),
                "tokens_out": sum(c["tokens_out"] for c in ok),
                "avg_tokens_in_flight": round(kv_tok),
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
            # THE capacity number: the highest level at which the SLO held.
            # Falls back to the held level when the SLO was never breached.
            "capacity_users": self.capacity_users or len(self.users),
            "baseline_p95_ms": self.baseline_p95,
            "slo": {"p95_x": self.cfg["slo_p95_x"], "p95_ms": self.cfg["slo_p95_ms"],
                     "err": self.cfg["slo_err"]},
            "max_users": len(self.users),
            "duration_s": round(dur, 1),
            "total_requests": self.total_requests,
            "total_tokens_out": sum(c["tokens_out"] for c in self.calls if c["ok"]),
            "steady": {**hold, "cpu_pct": avg("cpu_pct"), "mem_pct": avg("mem_pct"),
                        "power_w": avg("power_w"), "load1": avg("load1"),
                        "bw_gbs": avg("bw_gbs"), "kv_pct": avg("kv_pct")},
            "mem_mb_per_user": mem_slope_mb_per_user(samples),
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
            "capacity_users": self.capacity_users,
            "baseline_p95_ms": self.baseline_p95,
            "elapsed_s": round(time.time() - self.started_at, 1),
            "total_requests": self.total_requests,
            "latest": latest,
            "per_scenario": per_scenario,
            "timeline": samples,
            "error": self.error,
            "result": self.result,
        }
