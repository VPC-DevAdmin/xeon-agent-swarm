"""
The capacity test controller — the "speed test" state machine.

Ramp model: virtual users are added on a fixed cadence, each user being one of
the five fixed agent scenarios looping continuously. The ramp stops when the
system shows CONSISTENT saturation, then holds at that level to measure a clean
steady state, and reports a speed-test-style result.

WHAT THIS REPORTS — two metrics, different questions, different units, never
combined into one unlabelled number:

  service capability    closed loop. The largest concurrent session count at
                        which every workflow type meets its DECLARED deadline,
                        with the lower 95% bound on on-deadline success at or
                        above the target. Sessions.
  sustainable capacity  open loop. The highest offered rate whose clean,
                        durable completions the host keeps up with before the
                        backlog diverges. Clean workflows per second.

The closed-loop ramp also yields a stability ceiling: the level past which
added sessions stop being absorbed into a steady state. It carries no service
promise, so it is a DIAGNOSTIC and never the headline. When the ramp breaches
it, the run SCALES BACK DOWN to the last good level and measures there.

Every one of those numbers means something different depending on how the run
ENDED — see BOUNDARY_VERDICTS / CENSORING_VERDICTS / INVALID_VERDICTS below.
A run that ran out of CPU, clock, or dollars before the system showed its edge
produced a LOWER BOUND, and the result says so.

Stop conditions (first one to fire wins):
  unstable  latency kept climbing at FIXED load (window's 2nd half p80 over
            the 1st by > drift_tolerance) for 2 consecutive evaluations — the
            system stopped absorbing the load into a steady state. Scale down
            to the last certified level, drain the breach backlog, hold,
            measure. The buyer's-latency-budget view (slo_p95_x x baseline)
            is reported alongside as slo_capacity_users — an overlay, never
            the verdict.
  cpu       local mode: CPU sustained >= cpu_target for 2 consecutive intervals
  memory    local mode: system memory sustained >= mem_target — RAM can gate
            before cores do on big-model boxes
  kv        local mode: the engine's KV-cache pool sustained >= kv_target
            (scraped from SGLang /metrics; the truest "model memory full")
  errors    error rate over an interval exceeded error_rate_limit (hard stop)
  spend_guard  selected dollar circuit breaker reached (not capacity)

The throughput plateau (gain < plateau_frac × the expected linear gain, twice)
is recorded as knee_users — a diminishing-returns DIAGNOSTIC — but never stops
the ramp: capacity is the SLO/resource boundary, not the efficiency knee.

One test at a time, per process. Results are kept in memory and written to
data/capacity/ as JSON for history.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import statistics
import time

from collections import defaultdict
from collections import deque
from pathlib import Path

from backend.capacity.client import StepCaller
from backend.capacity.scenarios import (load_scenarios, load_tile, tile_sessions,
                                         load_e2e_workflows, load_e2e_tile,
                                         e2e_tile_sessions)
from backend.capacity.e2e import E2ERunner
from backend.capacity.telemetry import (ProcessCpuSampler, SystemSampler,
                                        find_pids, mem_slope_mb_per_user,
                                         sample_bandwidth_gbs, sample_kv_pct)
from backend.capacity.client import LOCAL_BASE
from backend.capacity.models import public_endpoint
from backend.capacity import stats as st
from backend.capacity import repro as repro_mod

RESULTS_DIR = Path("data/capacity")

# How a run ENDED decides what its numbers MEAN.
#
#   boundary    the ramp found the edge it went looking for: the system itself
#               stopped absorbing the load. The number is a measurement.
#   censoring   the run hit a limit of the harness, the budget, or the host
#               before any edge appeared. Everything it measured is a LOWER
#               BOUND on the real figure, because the levels above were never
#               tested. Reporting these as capacity sells a harness limit as a
#               system limit.
#   invalid     the run did not measure what it claims to measure. No number
#               it produced means anything.
BOUNDARY_VERDICTS = frozenset({"unstable", "errors", "queue_divergence"})
CENSORING_VERDICTS = frozenset({"cpu", "memory", "kv", "spend_guard", "budget",
                                "capped", "timeout", "interference", "stopped",
                                "generator_limit"})
INVALID_VERDICTS = frozenset({"workload_invalid", "harness_degraded"})

CENSOR_REASON = {
    "cpu": "host CPU saturated before a service boundary appeared",
    "memory": "host memory saturated before a service boundary appeared",
    "kv": "engine KV cache saturated before a service boundary appeared",
    "spend_guard": "dollar circuit breaker stopped the run",
    "budget": "dollar circuit breaker stopped the run",
    "capped": "configured ceiling reached with the system still healthy",
    "timeout": "time limit reached with the system still healthy",
    "interference": "other processes on the host saturated the CPU",
    "stopped": "stopped by hand before a boundary appeared",
    "generator_limit": "the load generator could not deliver the offered rate "
                       "— a harness limit, not host capacity",
}

# DB persistence of finished results (benchmark history). Module flag so unit
# tests can run without a schema; the JSON file fallback always happens.
PERSIST_TO_DB = True


def _scen_version() -> int:
    from backend.capacity.scenarios import benchmark_version
    return benchmark_version()


def _remote_model() -> str | None:
    from backend.capacity.client import REMOTE_MODEL
    return REMOTE_MODEL or None

DEFAULTS = dict(
    start_users=1,
    step_users=1,          # users added per interval
    step_interval_s=12.0,  # ramp cadence
    hold_s=20.0,           # steady-state measure window after saturation
    max_users=None,        # optional test-only compatibility guard; API leaves unset
    max_duration_s=None,   # optional test-only compatibility guard; API leaves unset
    cpu_target=90.0,       # local-mode saturation line
    mem_target=92.0,       # local-mode RAM saturation line (%)
    kv_target=90.0,        # local-mode KV-pool saturation line (%)
    slo_p95_x=3.0,         # SLO: a profile's p95 may grow to this multiple of ITS baseline
    slo_p95_ms=None,       # absolute p95 cap in ms (applies in addition to the multiplier)
    slo_err=0.05,          # SLO: max error rate per profile while a rung counts as "good"
    drift_tolerance=0.25,  # drift: 2nd half-window p80 may exceed the 1st by ≤25%
    cohort_maturity=0.8,   # fraction of the older half that must have COMPLETED to judge it
    harness_tolerance=0.005,  # lost writes/callbacks above this share invalidate the run
    invalid_tolerance=0.01,   # contract-violating units above this share invalidate the run
    # Capability: the declared SLO metric. A level passes only when the lower
    # one-sided 95% bound on each type's on-deadline success is >= target.
    service_class="interactive",
    capability_target=0.95,
    capability_min_samples=52,   # zero-failure samples needed to clear 95/95
    # Capacity: the open-loop metric. Offered rate steps upward; a level fails
    # when the lower bound on backlog growth is above zero twice over.
    load_model="closed",         # closed | open
    arrival_hold_s=45.0,
    arrival_step_factor=1.4,
    arrival_start_rate=2.0,
    arrival_max_rate=4000.0,
    max_backlog=20000,
    min_samples=3,         # completed calls per profile per interval to certify a rung
    warmup_s=5.0,          # rung-1 warm-up discarded before baselines are measured
    seed=None,             # benchmark seed (auto-generated when unset; always recorded)
    cache_mode="warm",     # warm: shared system preamble | cold: nothing prefix-cacheable
    e2e_timeout_s=300.0,   # per-workflow ceiling in end-to-end mode
    plateau_frac=0.25,     # gain < 25% of the expected linear gain, twice = knee
    error_rate_limit=0.10, # hard stop

    sample_interval_s=2.0,
    mock_ms=2000.0,
    mock_sigma=300.0,
    max_cost_usd=None,     # mandatory dollar circuit breaker for cloud runs
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
    def __init__(self, mode: str, scenario_ids: list[str], cfg: dict,
                 mix: str = "custom", extra_workflows: dict | None = None,
                 *, benchmark_target: str | None = None,
                 inference_backend: str | None = None,
                 e2e_router: dict | None = None,
                 endpoint: dict | None = None):
        self.mode = mode
        self.benchmark_target = benchmark_target or (
            "agent_host" if mode == "e2e" else "inference_engine")
        self.inference_backend = inference_backend or (
            "remote_mock" if mode == "e2e" else mode)
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
        all_scen = load_scenarios()
        # mix="tile": ramp one complete reference tile (ACU) per rung — the
        # comparable benchmark. mix="custom": round-robin over the selected
        # profiles (diagnosis / customer-specific planning; NON-comparable).
        self.mix = mix if mix in ("tile", "custom") else "custom"
        if mode == "e2e":
            # End-to-end runtime mode: the "profiles" are real workflows and one
            # call = one complete run through the orchestrator.
            all_scen = dict(load_e2e_workflows())
            # Agent definitions assigned to the benchmark become e2e workflows
            # with their own policy (tools/validator/budgets) — the reviewer's
            # "assign definitions to the benchmark or a planning mix".
            all_scen.update(extra_workflows or {})
            if self.mix == "tile":
                self.tile = load_e2e_tile()
                self.tile_assignment = e2e_tile_sessions()
                self.tile_size = len(self.tile_assignment)
                self.scenario_ids = list(self.tile.keys())
            else:
                self.tile, self.tile_assignment, self.tile_size = None, [], 0
                self.scenario_ids = ([s for s in scenario_ids if s in all_scen]
                                     or list(all_scen))
            self.scenarios = {sid: all_scen[sid] for sid in self.scenario_ids}
        elif self.mix == "tile":
            self.tile = load_tile()
            self.tile_assignment = tile_sessions()
            self.tile_size = len(self.tile_assignment)
            self.scenario_ids = list(self.tile.keys())
        else:
            self.tile = None
            self.tile_assignment = []
            self.tile_size = 0
            self.scenario_ids = [s for s in scenario_ids if s in all_scen] or list(all_scen)
        if mode != "e2e":
            self.scenarios = {sid: all_scen[sid] for sid in self.scenario_ids}

        self.phase = "starting"        # starting | ramping | holding | done | stopped | error
        self.verdict: str | None = None
        self.baseline_p95: float | None = None       # aggregate low-load p95 (context)
        self.baselines: dict[str, float] = {}        # per-profile p95 at rung 1 — the SLO refs
        self.capacity_users: int | None = None       # last session count where every SLO held
        self.capacity_tiles: int | None = None       # same, in tiles (tile mix)
        self.breach: dict | None = None              # which profile broke which limit
        self.knee_users: int | None = None           # efficiency knee (diagnostic, not a stop)
        self._accel_tiles = 1                        # geometric-climb batch size (tiles)
        self.slo_capacity_users: int | None = None   # OVERLAY: last level within the default
        self.slo_capacity_tiles: int | None = None   #   3x-baseline latency budget (not the verdict)
        self._eval_window_s: float | None = None     # e2e SLO window, derived at rung 1
        self._p95_streak = 0                         # consecutive tail-outside evals
        # Admission registry: work is measured from SUBMISSION, not only from
        # completion. Sampling completions alone hides the slowest and hung
        # units until they time out, so a level can read healthy while it is
        # deteriorating (survivorship bias in the latency distribution).
        self._inflight: dict[int, tuple[str, float]] = {}   # id -> (profile, admitted_at)
        self._admit_seq = 0
        self.invalid_units = 0                       # units that broke the workload contract
        self.harness: dict = {}                      # persistence/callback integrity counters
        # Capability (closed loop, deadline-bound) and capacity (open loop,
        # rate-bound) are separate numbers in separate units. Neither is
        # derived from the other and neither may be published unlabelled.
        self.capability_users: int | None = None
        self.capability_tiles: int | None = None
        self.capability_detail: dict = {}
        self.offered_rate: float = 0.0
        self.rejected = 0
        self._arrivals = 0        # schedule firings actually delivered (open loop)
        # Per-scenario running tallies for status(): the UI polls every couple
        # of seconds, and rebuilding them by scanning the whole call history
        # each poll was control-plane CPU charged to the system under test.
        self._scen_tally: dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "errors": 0, "last_error": None,
                     "ok_latencies": deque(maxlen=200)})
        self.rate_levels: list[dict] = []            # one record per offered rate
        self.capacity_wps: float | None = None
        self.capacity_detail: dict = {}
        self.failure_onset: dict | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.error: str | None = None
        self.endpoint = endpoint

        self.users: list[asyncio.Task] = []
        self.user_scenario: list[str] = []
        self.peak_users = 0
        self.calls: deque[dict] = deque(maxlen=100_000)   # every completed call
        self.samples: deque[dict] = deque(maxlen=1200)    # system telemetry
        self.total_requests = 0
        self.completed_requests = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.cost_usd = 0.0
        self._reserved_cost_usd = 0.0
        self._spend_lock = asyncio.Lock()
        self.capacity_levels: list[dict] = []
        self.result: dict | None = None

        if self.cfg.get("seed") is None:
            self.cfg["seed"] = random.randrange(1, 10**9)
        self.seed = int(self.cfg["seed"])
        self._user_call_n: dict[int, int] = defaultdict(int)
        self._engine_info: dict | None = None
        self._caller = StepCaller("remote_mock" if mode == "e2e" else mode,
                                  mock_ms=self.cfg["mock_ms"],
                                  mock_sigma=self.cfg["mock_sigma"],
                                  cache_mode=str(self.cfg.get("cache_mode") or "warm"),
                                  endpoint=endpoint)
        e2e_router = e2e_router or {}
        self._backend_model = (e2e_router.get("model_label")
                               or e2e_router.get("model_override"))
        self._e2e = E2ERunner(
            timeout_s=float(self.cfg["e2e_timeout_s"]),
            router_base_url=e2e_router.get("base_url"),
            router_api_key=e2e_router.get("api_key"),
            router_model=e2e_router.get("model_override"),
            router_provider=e2e_router.get("provider", "openai"),
        ) if mode == "e2e" else None
        self._sampler = SystemSampler()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        # Open-loop submissions get their own self-pruning set: appending one
        # task per unit to a list retains every task object for the run's
        # lifetime, which at sustained rates is an unbounded leak.
        self._open_tasks: set[asyncio.Task] = set()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def run(self):
        try:
            if self.inference_backend == "local":
                self._engine_info = await repro_mod.engine_info(LOCAL_BASE)
            sampler_task = asyncio.create_task(self._sample_loop())
            # A dead sampler must be LOUD: its exception is otherwise trapped
            # unobserved in the task while the run reports empty telemetry
            # (cost us a live run to learn this).
            sampler_task.add_done_callback(
                lambda t: (not t.cancelled() and t.exception()) and logging.getLogger(
                    __name__).error("telemetry sampler died: %r", t.exception()))
            self._tasks.append(sampler_task)
            if str(self.cfg["load_model"]) == "open":
                await self._rate_ramp()
                self._summarize_capacity()
            else:
                await self._ramp()
                await self._certify_capability()
        except Exception as exc:  # noqa: BLE001
            self.phase, self.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            self._stop.set()
            for t in [*self.users, *self._tasks, *self._open_tasks]:
                t.cancel()
            await asyncio.gather(*self.users, *self._tasks, *self._open_tasks,
                                 return_exceptions=True)
            await self._caller.aclose()
            self.ended_at = time.time()
            if self.phase not in ("error", "stopped"):
                self.phase = "done"
            await self._reconcile_harness()
            self._finalize()
            if PERSIST_TO_DB and self.result:
                await self._persist_db()

    def stop(self):
        self.phase = "stopped"
        self._stop.set()

    # ── virtual users ────────────────────────────────────────────────────────
    def _add_user(self, sid: str | None = None):
        idx = len(self.users)
        if sid is None:
            sid = self.scenario_ids[idx % len(self.scenario_ids)]
        self.user_scenario.append(sid)
        self.users.append(asyncio.create_task(self._user_loop(idx, sid)))
        self.peak_users = max(self.peak_users, len(self.users))

    def _add_tile(self) -> bool:
        """Add one complete reference tile (an optional test guard may refuse it)."""
        cap = self.cfg.get("max_users")
        if cap is not None and len(self.users) + self.tile_size > int(cap):
            return False
        for sid in self.tile_assignment:
            self._add_user(sid)
        return True

    def _remove_users(self, n: int):
        """Scale back down (SLO breach): capacity is measured at a level that
        WORKS, so the breached level's users are cancelled before the hold."""
        for _ in range(min(n, len(self.users) - 1)):
            task = self.users.pop()
            self.user_scenario.pop()
            task.cancel()

    def _price(self, tokens_in: int, tokens_out: int) -> float:
        if not self.endpoint:
            return 0.0
        return ((tokens_in * float(self.endpoint["input_per_mtok"]))
                + (tokens_out * float(self.endpoint["output_per_mtok"]))) / 1_000_000

    async def _reserve_spend(self, tokens_in: int, tokens_out: int) -> float | None:
        """Reserve estimated request cost so concurrent work respects the breaker."""
        estimate = self._price(tokens_in, tokens_out)
        limit = self.cfg.get("max_cost_usd")
        if not limit:
            return 0.0
        async with self._spend_lock:
            if self.cost_usd + self._reserved_cost_usd + estimate > float(limit):
                self.verdict = "spend_guard"
                self._stop.set()
                return None
            self._reserved_cost_usd += estimate
        return estimate

    async def _settle_spend(self, reserved: float, rec: dict) -> None:
        tokens_in = int(rec.get("tokens_in") or 0)
        tokens_out = int(rec.get("tokens_out") or 0)
        async with self._spend_lock:
            self._reserved_cost_usd = max(0.0, self._reserved_cost_usd - reserved)
            self.total_tokens_in += tokens_in
            self.total_tokens_out += tokens_out
            self.cost_usd += self._price(tokens_in, tokens_out)
            limit = self.cfg.get("max_cost_usd")
            if limit and self.cost_usd >= float(limit):
                self.verdict = "spend_guard"
                self._stop.set()

    async def _user_loop(self, idx: int, sid: str):
        """One virtual user: an agent SESSION, not a chatbot pinger.

        Context compounds within a turn (carry_context steps read everything
        produced so far, tool results get injected mid-step) and ACROSS turns
        for session_turns loops before the session resets — the growing-prefill,
        growing-KV signature that separates agents from flat chat traffic."""
        scenario = self.scenarios[sid]
        think = float(scenario.get("think_ms", 1000)) / 1000.0
        if self.mode == "e2e":
            await self._e2e_loop(idx, sid, scenario, think)
            return
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

    async def _e2e_loop(self, idx: int, wid: str, wf: dict, think: float):
        """One e2e session: submit a real workflow, await completion, think, repeat.

        Think time carries SEEDED +/-25% jitter (mean unchanged, deterministic
        per session from the run seed): AIMD batches otherwise create cohorts
        of phase-locked timers — observed live at ~870 sessions as in-flight
        oscillating 63<->771 while the latency body pulsed with the wave. A
        pulsing workload cannot pass a drift test; decohered timers can."""
        rng = random.Random((self.seed or 0) ^ (idx * 2654435761))
        while not self._stop.is_set():
            # Reserve a conservative workflow envelope before launch. Actual
            # usage replaces it on completion; this constrains concurrent spend.
            token_envelope = int((wf.get("budgets") or {}).get("max_total_tokens")
                                 or 50_000)
            reserved = await self._reserve_spend(
                int(token_envelope * 0.8), int(token_envelope * 0.2))
            if reserved is None:
                return
            self.total_requests += 1
            admit_key = self._admit(wid)
            rec = await self._e2e.run_workflow(wid, wf["query"], {
                "enabled_tools": wf.get("enabled_tools"),
                "validator_enabled": wf.get("validator_enabled", True),
                "budgets": wf.get("budgets"),
                "toolless": wf.get("toolless", False),
            }, timeout_s=self._profile_timeout_s(wid))
            t_submit = self._release(admit_key)
            rec.update(scenario=wid, step="workflow", user=idx, ts=time.time(),
                       t_submit=t_submit)
            self._check_contract(wid, rec)
            self._tally_call(rec)
            await self._settle_spend(reserved, rec)
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=think * (0.75 + 0.5 * rng.random()))
                return
            except asyncio.TimeoutError:
                pass

    async def _record_call(self, scenario, step, sid, idx, extra_tokens, label):
        """Budget-checked, recorded single LLM call (incl. tool continuations)."""
        if self._stop.is_set():
            return None
        reserved = await self._reserve_spend(
            int(step.get("prompt_tokens", 0)) + int(extra_tokens),
            int(step.get("max_tokens", 200)))
        if reserved is None:
            return None
        self.total_requests += 1
        self._user_call_n[idx] += 1
        vary_key = f"{self.seed}:{idx}:{self._user_call_n[idx]}"
        admit_key = self._admit(sid)
        rec = await self._caller.call(scenario, step, extra_context_tokens=extra_tokens,
                                      vary_key=vary_key)
        t_submit = self._release(admit_key)
        rec.update(scenario=sid, step=label, user=idx, ts=time.time(),
                   t_submit=t_submit)
        self._tally_call(rec)
        await self._settle_spend(reserved, rec)
        return rec

    # ── telemetry ────────────────────────────────────────────────────────────
    def _cpu_groups(self) -> dict[str, list[int]]:
        """Named pid groups for CPU attribution. Rebuilt each sample so
        late-started components (engine container, mock router) join when
        they appear; a dead pid simply reads 0."""
        groups: dict[str, list[int]] = {"control": [os.getpid()]}
        try:
            from backend import workerpool as wp
            groups["executors"] = [p.pid for p in wp._procs if p.poll() is None]
        except Exception:  # noqa: BLE001
            groups["executors"] = []
        try:
            from backend.capacity import mockrouter
            if mockrouter._proc is not None and mockrouter._proc.poll() is None:
                groups["mock_router"] = [mockrouter._proc.pid]
        except Exception:  # noqa: BLE001
            pass
        if self.inference_backend == "local":
            if self._engine_pids is None:
                self._engine_pids = find_pids("sglang.launch_server")
            if self._engine_pids:
                groups["engine"] = self._engine_pids
        return groups

    async def _sample_loop(self):
        # Bandwidth/KV are local-mode readings; stop attempting after repeated
        # misses so we never spawn perf / scrape a dead endpoint in a tight loop.
        bw_misses = 0
        kv_misses = 0
        proc_cpu = ProcessCpuSampler()
        self._engine_pids: list[int] | None = None
        while not self._stop.is_set():
            s = self._sampler.sample()
            # Attribution: who is burning the box — the agent runtime's own
            # processes, the inference engine, or everything else. Same basis
            # as cpu_pct, so components stack under the host line.
            try:
                by = proc_cpu.sample(self._cpu_groups())
                if by is not None and s.get("cpu_pct") is not None:
                    by["other"] = round(max(0.0, s["cpu_pct"] - sum(by.values())), 1)
                    s["cpu_by"] = by
            except Exception:  # noqa: BLE001 — attribution must never kill telemetry
                pass
            s["users"] = len(self.users)
            s.update(self._window_stats(self.cfg["sample_interval_s"] * 5))
            # Includes work waiting on router, database, or execution resources;
            # this is the observable backlog for an async orchestration host.
            s["in_flight"] = max(0, self.total_requests - self.completed_requests)
            # Censored-work telemetry: units admitted and not yet finished, and
            # the age of the oldest one. Completion-only sampling cannot see
            # either, which is how a deteriorating level reads healthy.
            s["inflight_admitted"] = len(self._inflight)
            s["oldest_inflight_s"] = self._oldest_inflight_s()
            s["bw_gbs"] = None
            s["kv_pct"] = None
            if self.inference_backend == "local":
                if bw_misses < 3:
                    s["bw_gbs"] = await sample_bandwidth_gbs(
                        max(0.5, self.cfg["sample_interval_s"] - 1.0))
                    bw_misses = 0 if s["bw_gbs"] is not None else bw_misses + 1
                if kv_misses < 3:
                    s["kv_pct"] = await sample_kv_pct(LOCAL_BASE)
                    kv_misses = 0 if s["kv_pct"] is not None else kv_misses + 1
            self.samples.append(s)
            await asyncio.sleep(self.cfg["sample_interval_s"])

    def _admit(self, sid: str) -> int:
        """Register a unit as admitted. Returns its in-flight key."""
        self._admit_seq += 1
        self._inflight[self._admit_seq] = (sid, time.time())
        return self._admit_seq

    def _release(self, key: int) -> float:
        """Deregister a finished unit. Returns the time it was admitted."""
        entry = self._inflight.pop(key, None)
        return entry[1] if entry else time.time()

    def _oldest_inflight_s(self) -> float | None:
        if not self._inflight:
            return 0.0
        now = time.time()
        return round(now - min(t for _sid, t in self._inflight.values()), 2)

    def _check_contract(self, sid: str, rec: dict) -> None:
        """Enforce the declared workflow contract on a completed unit.

        A unit whose trace falls outside the contract is neither a success nor
        a capacity failure: it is workload-invalid, excluded from the latency
        and error statistics, and counted separately. Enough of them
        invalidates the run, because the benchmark no longer measured the unit
        it declared."""
        wf = self.scenarios.get(sid) or {}
        spec = (wf.get("contract_live") if self.inference_backend != "remote_mock"
                else wf.get("contract")) or wf.get("contract_live")
        trace = rec.get("trace")
        if not spec or not trace or not rec.get("ok"):
            return
        for field, bounds in spec.items():
            value = trace.get(field)
            if value is None:
                continue
            low, high = bounds
            if not (low <= value <= high):
                rec["ok"] = False
                rec["invalid"] = True
                rec["error"] = f"contract violation: {field}={value} outside [{low}, {high}]"
                self.invalid_units += 1
                return

    def _recent(self, cut: float) -> list[dict]:
        """Calls newer than `cut`, walking the time-ordered deque from the
        newest end and stopping at the boundary — O(window), not O(history).
        At 100k retained calls, full scans per evaluation were the control
        plane's next heat source."""
        out: list[dict] = []
        for c in reversed(self.calls):
            if c["ts"] < cut:
                break
            out.append(c)
        return out

    def _scenario_window(self, sid: str, window_s: float) -> dict:
        """Per-profile stats over the window: the unit of SLO evaluation."""
        cut = time.time() - window_s
        recent = [c for c in self._recent(cut)
                  if c["scenario"] == sid and not c.get("invalid")]
        ok = [c for c in recent if c["ok"]]
        return {
            "n": len(recent),
            "p95_ms": _pct([c["latency_ms"] for c in ok], 95),
            "err_rate": round(1 - len(ok) / len(recent), 3) if recent else 0.0,
        }

    def _profile_timeout_s(self, sid: str) -> float:
        """Per-workflow ceiling: a GENEROUS ABSOLUTE bound, deliberately not
        derived from the SLO. Stability is the verdict; a timeout tied to a
        latency budget would manufacture the collapse instead of observing it.
        The ceiling exists only so a hung workflow cannot park a session
        forever — timeouts that do fire count as errors, and an error cascade
        is the closed-loop analog of a wait explosion."""
        return float(self.cfg["e2e_timeout_s"])

    def _slo_limit(self, sid: str) -> float | None:
        """A profile's p95 limit: multiplier x its own baseline, tightened by the
        absolute cap when configured."""
        base = self.baselines.get(sid)
        rel = base * float(self.cfg["slo_p95_x"]) if base else None
        abs_ms = self.cfg["slo_p95_ms"]
        if rel and abs_ms:
            return min(rel, float(abs_ms))
        return rel or (float(abs_ms) if abs_ms else None)

    def _evaluate_rung(self, window_s: float) -> tuple[str, dict | None]:
        """Certify the current rung on STABILITY, not a latency multiplier.

        Capacity is the load the system absorbs into a steady state. A closed-
        loop rig cannot show a queue explosion (in-flight is capped at N), so
        the intrinsic failure signals at fixed load are:
          - latency drift: the p80 of the window's 2nd half exceeds the p80
            of the 1st half by more than drift_tolerance — the system is
            still accumulating delay at constant load;
          - per-profile error rate over slo_err (timeouts are the closed-loop
            analog of a wait explosion).
        The old p95 <= slo_p95_x x baseline test is an OVERLAY (a buyer's
        latency budget), reported as slo_capacity_users, never the verdict.

        This is a two-sample comparison of half-window quantiles against a
        tolerance, not a test for stationarity in the formal sense: it detects
        a level that is still accumulating delay, and makes no claim about the
        distribution being time-invariant.

        Returns (state, breach): 'good' (no drift + errors bounded, enough
        samples), 'bad' (breach names the mechanism), or 'inconclusive'."""
        min_n = int(self.cfg["min_samples"])
        state = "good"
        for sid in self.scenario_ids:
            # A profile with no sessions assigned is not in play at this level.
            # Requiring samples from it makes certification impossible for any
            # custom mix whose session count is below its profile count.
            if self.user_scenario and sid not in self.user_scenario:
                continue
            s = self._scenario_window(sid, window_s)
            if s["err_rate"] > float(self.cfg["slo_err"]) and s["n"] >= min_n:
                return "bad", {"profile": sid, "metric": "error_rate",
                                "value": s["err_rate"],
                                "limit": float(self.cfg["slo_err"])}
            if s["n"] < min_n:
                state = "inconclusive"
        # Trend test over ADMISSION COHORTS. Two rules make the comparison
        # honest. Units are grouped by when they were submitted, not when they
        # finished, and only units admitted at the CURRENT level are eligible,
        # so completions from an easier level cannot certify a harder one. The
        # older half must also be mature (most of its work has finished),
        # because judging a half whose slow units are still running measures
        # the survivors rather than the level.
        now = time.time()
        low = max(now - window_s, self._rung_t0)
        span = now - low
        if span < 0.5 * window_s:
            return "inconclusive", None          # cohort too young to judge
        mid = low + span / 2.0

        # One newest-first walk covers both halves: a call admitted at or
        # after `low` completed at or after `low`, so stopping the walk at
        # ts < low loses nothing. This replaced a full scan of the 100k-deep
        # deque per half per evaluation — control-plane CPU that was being
        # charged to the system under test.
        recent = self._recent(low)

        def _cohort(a: float, b: float) -> tuple[list[float], int]:
            done = [c["latency_ms"] for c in recent
                    if c.get("ok") and not c.get("invalid")
                    and a <= c.get("t_submit", c["ts"]) < b]
            censored = sum(1 for _sid, t in self._inflight.values() if a <= t < b)
            return done, censored

        h1, cens1 = _cohort(low, mid)
        h2, _cens2 = _cohort(mid, now)
        need = max(min_n, 2)      # a single-sample quantile is noise, not a trend
        if len(h1) < need or len(h2) < need:
            return "inconclusive", None
        maturity = len(h1) / max(1, len(h1) + cens1)
        if maturity < float(self.cfg["cohort_maturity"]):
            return "inconclusive", None          # slow work has not landed yet
        tol = 1.0 + float(self.cfg["drift_tolerance"])

        # Oldest in-flight age must not trend upward. Completion-time sampling
        # cannot see a unit that never completes, so a stalling level can hold
        # its visible latency flat while its queue ages. The sampler records
        # the age directly, and the two halves of the window are compared the
        # same way the latency body is.
        ages1 = [s["oldest_inflight_s"] for s in self.samples
                 if s.get("oldest_inflight_s") is not None
                 and low <= s["ts"] < mid]
        ages2 = [s["oldest_inflight_s"] for s in self.samples
                 if s.get("oldest_inflight_s") is not None and s["ts"] >= mid]
        if len(ages1) >= 2 and len(ages2) >= 2:
            a1, a2 = statistics.median(ages1), statistics.median(ages2)
            floor_s = max(2.0, (_pct(h1, 80) or 0) / 1000.0)
            if a1 > 0 and a2 > max(a1 * tol, floor_s):
                return "bad", {"profile": "aggregate", "metric": "work_aging",
                                "value": round(a2, 1),
                                "limit": round(max(a1 * tol, floor_s), 1),
                                "baseline_ms": round(a1 * 1000, 1)}
        # PRIMARY: p80 drift. The p95 of a half-window is decided by a
        # handful of tail samples, and batch-cohort waves made it oscillate
        # 3s<->10s at ~900 sessions — halving the ramp accelerator on noise.
        # p80 tracks the body of the distribution and decides the ramp.
        p80_1, p80_2 = _pct(h1, 80), _pct(h2, 80)
        if p80_1 and p80_2 and p80_2 > p80_1 * tol:
            self._p95_streak = 0
            return "bad", {"profile": "aggregate", "metric": "latency_unstable",
                            "value": round(p80_2, 1), "limit": round(p80_1 * tol, 1),
                            "baseline_ms": round(p80_1, 1)}
        # SECONDARY: the p95 tail only condemns a rung when it is outside
        # tolerance CONSISTENTLY (3 consecutive evaluations) — a persistent
        # tail divergence with a stable body is real; a single spike is not.
        p95_1, p95_2 = _pct(h1, 95), _pct(h2, 95)
        if p95_1 and p95_2 and p95_2 > p95_1 * tol:
            self._p95_streak += 1
            if self._p95_streak >= 3:
                return "bad", {"profile": "aggregate", "metric": "tail_unstable",
                                "value": round(p95_2, 1),
                                "limit": round(p95_1 * tol, 1),
                                "baseline_ms": round(p95_1, 1)}
        else:
            self._p95_streak = 0
        return state, None

    def _window_stats(self, window_s: float) -> dict:
        cut = time.time() - window_s
        recent = [c for c in self._recent(cut) if not c.get("invalid")]
        ok = [c for c in recent if c["ok"]]
        lat = [c["latency_ms"] for c in ok]
        toks = sum(c["tokens_out"] for c in ok)
        tokens_in = sum(c.get("tokens_in", 0) for c in ok)
        tokens_out = sum(c.get("tokens_out", 0) for c in ok)
        cost = self._price(tokens_in, tokens_out)
        return {
            "tps": round(toks / window_s, 1),
            "rpm": round(len(ok) * 60.0 / window_s, 1),
            "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95),
            "err_rate": round(1 - len(ok) / len(recent), 3) if recent else 0.0,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost, 6),
            "cost_per_hour": round(cost * 3600 / window_s, 4),
        }

    def _record_level(self, phase: str, stats: dict, slo_state: str) -> None:
        row = {
            "phase": phase,
            "users": len(self.users),
            "tiles": (len(self.users) // self.tile_size if self.tile_size else None),
            "slo_state": slo_state,
            "p95_ms": stats.get("p95_ms"),
            "err_rate": stats.get("err_rate"),
            "tps": stats.get("tps"),
            "rpm": stats.get("rpm"),
            "tokens_in": stats.get("tokens_in", 0),
            "tokens_out": stats.get("tokens_out", 0),
            "incremental_cost_usd": stats.get("cost_usd", 0.0),
            "cumulative_cost_usd": round(self.cost_usd, 6),
            "projected_cost_per_hour": stats.get("cost_per_hour", 0.0),
        }
        if (self.capacity_levels and self.capacity_levels[-1]["phase"] == phase
                and self.capacity_levels[-1]["users"] == len(self.users)):
            self.capacity_levels[-1] = row
        else:
            self.capacity_levels.append(row)

    # ── the ramp ─────────────────────────────────────────────────────────────
    async def _ramp(self):
        if self.mix == "tile":
            self._add_tile()
        else:
            for _ in range(int(self.cfg["start_users"])):
                self._add_user()
        self.phase = "ramping"
        interval = float(self.cfg["step_interval_s"])
        # Warm-up: let rung 1 run (caches, allocators, thermals settle), then
        # discard those calls/samples so baselines reflect steady behavior.
        warm = float(self.cfg.get("warmup_s") or 0)
        self._t_measure = time.time()
        if warm > 0:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=warm)
                return
            except asyncio.TimeoutError:
                pass
            self.calls.clear()
            self.samples.clear()
            self._t_measure = time.time()
        prev_tps: float | None = None
        prev_users = int(self.cfg["start_users"])
        self._baselines_ready = False
        self._rung_t0 = time.time()   # when the current rung's load level began
        cpu_hot = 0
        mem_hot = 0
        kv_hot = 0
        flat = 0
        slo_bad = 0
        self._last_bad_count = 0.0

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
            bg_vals = [s["cpu_by"]["other"] for s in self.samples
                       if s.get("cpu_by") and "other" in s["cpu_by"]
                       and s["ts"] >= cut]
            avg_bg = statistics.mean(bg_vals) if bg_vals else None
            elapsed = time.time() - self.started_at

            # Per-profile baselines are measured at rung 1 (the healthy
            # reference, VMmark-style): hold rung 1 until every profile has
            # min_samples, then lock its baseline. No SLO evaluation and no
            # ramping until baselines exist (bounded by 4 intervals).
            if not self._baselines_ready:
                elapsed_rung1 = time.time() - self._t_measure
                window = elapsed_rung1  # whole rung-1 period so far
                missing = []
                for sid in self.scenario_ids:
                    s = self._scenario_window(sid, window)
                    if s["n"] >= int(self.cfg["min_samples"]) and s["p95_ms"] is not None:
                        self.baselines.setdefault(sid, s["p95_ms"])
                    else:
                        missing.append(sid)
                # e2e workflows can outlast several intervals, so the rung-1
                # bound must cover at least one full workflow (or its timeout);
                # 4 intervals is only enough for fast synthetic traces.
                baseline_bound = (float(self.cfg["e2e_timeout_s"]) + interval
                                  if self.mode == "e2e" else 4 * interval)
                if not missing or elapsed_rung1 > baseline_bound:
                    self._baselines_ready = True
                    if self.baseline_p95 is None:
                        self.baseline_p95 = (stats["p95_ms"]
                                             if stats["p95_ms"] is not None
                                             else (round(statistics.median(
                                                 self.baselines.values()), 1)
                                                   if self.baselines else None))
                    if self.mode == "e2e":
                        # Declared rule, recorded in the repro block: the SLO
                        # evaluation window must be able to contain complete
                        # workflows, or no rung can ever certify. Derived from
                        # measured rung-1 durations, not tuned per run.
                        rung1_ok = [c["latency_ms"] / 1000.0
                                    for c in self.calls if c["ok"]]
                        self._eval_window_s = max(
                            3 * interval,
                            2.0 * statistics.median(rung1_ok) if rung1_ok else 0)
                else:
                    # The rung-1 hold must still honor the wall-clock ceiling —
                    # otherwise a dead backend parks the run here silently.
                    if (self.cfg.get("max_duration_s") is not None
                            and elapsed > float(self.cfg["max_duration_s"])):
                        self.verdict = "timeout"
                        await self._hold()
                        return
                    continue  # keep measuring rung 1; do not ramp yet

            # Evaluate over a wider window than the ramp cadence: slow-cadence
            # profiles (long think times, tool waits) need room to produce
            # min_samples, or every rung reads as inconclusive. In e2e mode the
            # window tracks the CURRENT measured workflow duration — a window
            # frozen at rung-1 geometry silently broke certification when
            # workflow durations drifted 200x during the 27k-session ramp
            # (top-level windows were judging completions launched minutes
            # earlier at lower levels).
            eval_window = self._current_eval_window(interval)
            rung_state, breach = self._evaluate_rung(eval_window)
            # Certification happens only at TILE BOUNDARIES, where the mix is
            # complete and rungs are comparable; sessions are introduced one at
            # a time between boundaries (the declared tile rotation), so the
            # ramp approaches the wall gently instead of detonating a whole
            # tile of backlog on arrival.
            at_boundary = (self.mix != "tile" or not self.tile_size
                           or len(self.users) % self.tile_size == 0)
            if (rung_state == "inconclusive"
                    and time.time() - self._rung_t0 > 3 * eval_window):
                # The rung dwelled three full windows and some profile still
                # could not produce min_samples — it has effectively stopped
                # delivering work. That is a failure of the rung, not an
                # unknown: name the starved profile and condemn the rung.
                starved = next(
                    (sid for sid in self.scenario_ids
                     if self._scenario_window(sid, eval_window)["n"]
                     < int(self.cfg["min_samples"])), self.scenario_ids[0])
                rung_state = "bad"
                breach = {"profile": starved, "metric": "no_samples",
                          "value": 0.0,
                          "limit": float(self.cfg["min_samples"])}
            if rung_state == "good":
                if at_boundary:
                    self.capacity_users = len(self.users)
                    if self.mix == "tile":
                        self.capacity_tiles = len(self.users) // max(1, self.tile_size)
                    self._update_slo_overlay(eval_window)
                slo_bad = 0
                # A transient bad that a later evaluation supersedes was a
                # blip, not the boundary: breach describes what ENDED the run.
                self.breach = None
            elif rung_state == "bad":
                # Consecutive evaluations share most of their window, so a
                # one-off transient (e.g. a step to a new stable plateau) can
                # read bad twice in a row. Count a bad toward the verdict only
                # when a FULL window has passed since the last counted one —
                # two independent windows of sustained failure, with the load
                # held constant in between (the bad branch below never adds).
                if time.time() - self._last_bad_count >= eval_window:
                    slo_bad = slo_bad + 1
                    self._last_bad_count = time.time()
                    self._accel_tiles = 1    # CONFIRMED bad: probe, don't leap
                else:
                    # transient wobble: back off multiplicatively (AIMD), don't
                    # rebuild the whole doubling ladder from 1
                    self._accel_tiles = max(1, self._accel_tiles // 2)
                self.breach = breach
            # inconclusive: neither certify nor condemn — hold judgment
            self._record_level("ramp", stats, rung_state)

            # CPU/RAM are meaningful for real agent workflows and direct local
            # inference. Synthetic remote traces do not exercise either host.
            host_is_target = (self.benchmark_target != "inference_engine"
                              or self.inference_backend == "local")
            cpu_hot = cpu_hot + 1 if (
                host_is_target and avg_cpu is not None
                and avg_cpu >= self.cfg["cpu_target"]) else 0
            mem_hot = mem_hot + 1 if (
                host_is_target and avg_mem is not None
                and avg_mem >= self.cfg["mem_target"]) else 0
            kv_hot = kv_hot + 1 if (
                self.inference_backend == "local" and avg_kv is not None
                and avg_kv >= self.cfg["kv_target"]) else 0
            # Relative plateau — DIAGNOSTIC, not a stop. Capacity is defined by
            # the SLO boundary (plus resource/error saturation); a throughput
            # knee with SLOs green and the host idle just means marginal gain
            # is falling, and stopping there reports the shape of the ramp,
            # not the limit. The knee is recorded once (efficiency marker) and
            # the ramp continues to a real boundary. The relative rule (gain
            # vs PERFECT scaling for the users just added) avoids the fixed-%
            # trap of firing on 1/N arithmetic alone.
            frac = float(self.cfg["plateau_frac"] or 0)
            if prev_tps is not None and prev_tps > 0 and frac > 0:
                gain = (stats["tps"] - prev_tps) / prev_tps
                added = max(0, len(self.users) - prev_users)
                expected = added / max(1, prev_users)
                flat = flat + 1 if (added > 0 and gain < frac * expected) else 0
                if (flat >= 2 and self.knee_users is None
                        and len(self.users) > int(self.cfg["start_users"])):
                    self.knee_users = len(self.users)
            prev_tps = stats["tps"]
            prev_users = len(self.users)

            if slo_bad >= 2:
                # Name the mechanism the breach recorded: latency climbing at
                # fixed load (or a starved profile) is instability; a
                # per-profile error breach is an error boundary.
                self.verdict = ("unstable"
                                if (self.breach or {}).get("metric")
                                in ("latency_unstable", "tail_unstable",
                                    "work_aging", "no_samples")
                                else "errors")
            elif cpu_hot >= 2:
                # Attribution-aware: when the host crosses the CPU line but
                # the MAJORITY of the burn is processes outside the benchmark,
                # reporting 'cpu' would sell interference as capacity
                # (observed live: verdict cpu at 24 sessions with executors at
                # 1.2% and 'other' at 90.5%). Call it what it is.
                if (avg_bg is not None and avg_cpu
                        and avg_bg >= 0.5 * avg_cpu):
                    self.verdict = "interference"
                    self.breach = {"profile": "host",
                                   "metric": "background_cpu",
                                   "value": round(avg_bg, 1),
                                   "limit": round(0.5 * avg_cpu, 1)}
                else:
                    self.verdict = "cpu"
            elif mem_hot >= 2:
                self.verdict = "memory"
            elif kv_hot >= 2:
                self.verdict = "kv"
            elif (stats["err_rate"] > self.cfg["error_rate_limit"]
                  and rung_state != "bad"):
                # Mass-failure hard stop — but when the rung is already bad
                # with a NAMED per-profile breach, the SLO path is the more
                # informative verdict (it names the profile and scales back to
                # certified capacity), so let it resolve instead.
                self.verdict = "errors"
                if self.breach is None:
                    # Name the mechanism, not just the count: the profile with
                    # the worst error share in the current window.
                    worst, worst_rate = None, 0.0
                    for sid in self.scenario_ids:
                        s = self._scenario_window(sid, eval_window)
                        if s["n"] and s["err_rate"] >= worst_rate:
                            worst, worst_rate = sid, s["err_rate"]
                    if worst:
                        self.breach = {"profile": worst, "metric": "error_rate",
                                       "value": round(worst_rate, 3),
                                       "limit": float(self.cfg["error_rate_limit"])}
            elif (self.cfg.get("max_users") is not None
                  and len(self.users) >= int(self.cfg["max_users"])):
                self.verdict = "capped"
            elif (self.cfg.get("max_duration_s") is not None
                  and elapsed > float(self.cfg["max_duration_s"])):
                self.verdict = "timeout"

            if self.verdict:
                # breach describes the verdict's mechanism. A transient bad
                # evaluation recorded just before an unrelated stop (capped,
                # cpu, timeout, ...) must not linger as if it ended the run.
                if self.verdict not in ("unstable", "errors", "interference"):
                    self.breach = None
                if (self.verdict in ("unstable", "errors") and self.capacity_users
                        and self.capacity_users < len(self.users)):
                    # Measure capacity at the last CERTIFIED level, and let the
                    # breach-level backlog drain before the steady window opens
                    # — otherwise the hold measures the wreckage, not the level.
                    self._remove_users(len(self.users) - self.capacity_users)
                    await self._drain()
                await self._hold()
                return

            if rung_state == "bad":
                continue                # never add load onto a failing level
            if at_boundary and rung_state != "good":
                # Steady-state-per-level: a level must CERTIFY before the ramp
                # advances. Advancing on an inconclusive reading resets the
                # cohort clock every interval, so no level ever accumulates
                # enough of its own admitted work to be judged, and the run
                # ramps to its ceiling having measured nothing. Inconclusive
                # levels dwell; the starvation bound above condemns a level
                # that never produces samples.
                continue

            if self.mix == "tile":
                # Geometric climb with headroom gating: while the certified
                # boundary is green AND there is obvious headroom, add tiles in
                # doubling batches (1,2,4,8) — one session per tick would crawl
                # for an hour when the wall is nowhere in sight. The moment
                # headroom shrinks or an evaluation is anything but good, drop
                # to single-session probing. Bulk adds are whole tiles from a
                # boundary, so certification/comparability are untouched.
                cap = self.cfg.get("max_users")
                if cap is not None and len(self.users) >= int(cap):
                    self.verdict = "capped"
                    await self._hold()
                    return
                if (at_boundary and rung_state == "good"
                        and self._headroom(stats)):
                    # Proportional cap: up to 50% of the current level per
                    # step (min 8 tiles) — with the wall far away, small
                    # batches only waste wall-clock. Adds are STAGGERED across
                    # ~5s so a batch doesn't launch a thundering herd whose
                    # spike the drift check then measures.
                    max_tiles = max(8, (len(self.users) // max(1, self.tile_size)) // 2)
                    n = min(self._accel_tiles, max_tiles) * self.tile_size
                    pause = min(0.25, 5.0 / max(1, n))
                    for _ in range(n):
                        if cap is not None and len(self.users) >= int(cap):
                            break
                        self._add_user(
                            self.tile_assignment[len(self.users) % self.tile_size])
                        if self._stop.is_set():
                            break
                        await asyncio.sleep(pause)
                    self._accel_tiles = min(max_tiles, self._accel_tiles * 2)
                else:
                    # probing step — accel only decays on BAD evaluations, not
                    # on ordinary mid-tile ticks
                    self._add_user(
                        self.tile_assignment[len(self.users) % self.tile_size])
            else:
                for _ in range(int(self.cfg["step_users"])):
                    if (self.cfg.get("max_users") is None
                            or len(self.users) < int(self.cfg["max_users"])):
                        self._add_user()
            self._rung_t0 = time.time()

    def _current_eval_window(self, interval: float) -> float:
        """e2e SLO window sized to what workflows take NOW: 2x the median of
        the most recent completed workflows (declared rule, same as the rung-1
        derivation — just re-derived continuously so certification geometry
        follows the workload as latency grows with load)."""
        if self.mode != "e2e":
            return 3 * interval
        import itertools
        lats = [c["latency_ms"] / 1000.0
                for c in itertools.islice(reversed(self.calls), 200) if c["ok"]]
        if not lats:
            return self._eval_window_s or 3 * interval
        win = max(3 * interval, 2.0 * statistics.median(lats))
        self._eval_window_s = win          # recorded in the repro block
        return win

    def _headroom(self, stats: dict) -> bool:
        """Obvious distance from the boundaries that actually stop the ramp:
        host CPU under half the saturation target, and latency under half the
        workflow timeout (sanity ceiling). A baseline-multiple check proved
        wrong here: closed-loop latency is ALWAYS a few x the empty-machine
        baseline at scale while remaining perfectly flat — it throttled the
        ramp to single-session probing at 500 sessions with p95 flat at 5s.
        Wall proximity is the drift machinery's job (bad evaluations halve
        the batch and gate adds), not this heuristic's."""
        if self.samples:
            cpu = self.samples[-1].get("cpu_pct")
            # Batch while CPU < 85% of the saturation target: the final
            # approach is guarded by the drift test, per-profile errors, and
            # interference check, so the gate only needs to prevent leaping
            # PAST the wall — the earlier 50% setting left an hour of
            # single-session creep between half-load and the cpu verdict.
            if cpu is not None and cpu >= 0.85 * float(self.cfg["cpu_target"]):
                return False
        p95 = stats.get("p95_ms")
        if p95 and p95 >= 0.5 * float(self.cfg["e2e_timeout_s"]) * 1000:
            return False
        return True

    def _update_slo_overlay(self, window_s: float) -> None:
        """Buyer's-latency-budget OVERLAY (default 3x each profile's baseline):
        recorded alongside the stability capacity, never the verdict. Only
        advances when every profile has enough samples and is inside its
        budget at this level."""
        min_n = int(self.cfg["min_samples"])
        for sid in self.scenario_ids:
            s = self._scenario_window(sid, window_s)
            limit = self._slo_limit(sid)
            if not limit or s["n"] < min_n or s["p95_ms"] is None                     or s["p95_ms"] > limit:
                return
        self.slo_capacity_users = len(self.users)
        if self.mix == "tile" and self.tile_size:
            self.slo_capacity_tiles = len(self.users) // self.tile_size

    async def _drain(self):
        """After scaling back from a breached level, wait for the breach-level
        backlog to finish (in-flight <= remaining sessions) so the steady
        window measures the certified level, not the wreckage draining out.
        Bounded by the per-workflow ceiling."""
        limit = time.time() + float(self.cfg["e2e_timeout_s"])
        while (time.time() < limit and not self._stop.is_set()
               and self.total_requests - self.completed_requests > len(self.users)):
            await asyncio.sleep(1.0)

    async def _hold(self):
        """Hold at the saturation level and measure a clean steady state. In
        e2e mode the hold must span the derived evaluation window so the
        steady-state figures contain complete workflows."""
        self.phase = "holding"
        hold_s = float(self.cfg["hold_s"])
        if self.mode == "e2e" and self._eval_window_s:
            hold_s = max(hold_s, self._eval_window_s)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=hold_s)
        except asyncio.TimeoutError:
            pass
        self._hold_window_s = hold_s
        if self.verdict != "spend_guard":
            steady = self._window_stats(hold_s)
            state, _ = self._evaluate_rung(hold_s)
            self._record_level("steady", steady, state)

    async def _reconcile_harness(self):
        """Sum persistence and callback failures across every process.

        A lost write or a lost completion callback is a benchmark failure, not
        an agent failure, and the two are indistinguishable in the latency
        record. Counts ride in the result; past the tolerance the run stops
        being a capacity measurement at all."""
        try:
            from backend import workerpool as wp
            counters = await wp.collect_counters()
        except Exception as exc:  # noqa: BLE001
            self.harness = {"error": f"{type(exc).__name__}: {exc}"}
            return
        total = max(1, self.total_requests)
        lost = int(counters.get("persist_failures", 0)) + \
            int(counters.get("callback_failures", 0))
        counters["lost_fraction"] = round(lost / total, 5)
        counters["invalid_units"] = self.invalid_units
        counters["ok"] = lost <= total * float(self.cfg["harness_tolerance"])
        self.harness = counters
        if self.invalid_units > self.total_requests * float(self.cfg["invalid_tolerance"]):
            self.verdict = "workload_invalid"
            self.breach = {"profile": "workload", "metric": "contract_violations",
                           "value": self.invalid_units,
                           "limit": round(self.total_requests
                                          * float(self.cfg["invalid_tolerance"]), 1)}
        if not counters["ok"]:
            self.verdict = "harness_degraded"
            self.breach = {"profile": "harness", "metric": "lost_records",
                           "value": lost,
                           "limit": round(total * float(self.cfg["harness_tolerance"]), 1)}


    # ── metric 1: service capability (closed loop, deadline-bound) ───────────

    def _deadline_s(self, sid: str) -> float | None:
        """Declared capability deadline for a workflow type, in seconds.

        Deadlines come from the workload definition and are never recalculated
        from the system under test: a deadline derived from a machine's own
        speed hands a slow machine an easy target."""
        spec = (self.scenarios.get(sid) or {}).get("deadlines") or {}
        value = spec.get(str(self.cfg["service_class"]))
        return float(value) if value else None

    def deadlines_configured(self) -> bool:
        return any(self._deadline_s(sid) for sid in self.scenario_ids)

    def _capability_cohort(self, sid: str, since: float) -> tuple[int, int, int]:
        """(successes, decided, pending) for one type at the current level.

        A cohort member is decided when it finished or when its deadline has
        already passed. A unit still running inside its deadline is pending and
        counts as neither, which is what keeps a slow level from looking clean
        simply because its slow work has not landed yet."""
        deadline = self._deadline_s(sid)
        if deadline is None:
            return 0, 0, 0
        successes = decided = 0
        # _recent walks from the newest end and stops at ts < since; every
        # call with t_submit >= since also has ts >= since, so none is missed.
        for c in self._recent(since):
            if c.get("scenario") != sid or c.get("invalid"):
                continue
            if c.get("t_submit", c["ts"]) < since:
                continue
            decided += 1
            on_time = c.get("latency_ms", 0) <= deadline * 1000
            durable = c.get("durable", True)
            if c.get("ok") and on_time and durable:
                successes += 1
        now = time.time()
        pending = 0
        for profile, admitted in self._inflight.values():
            if profile != sid or admitted < since:
                continue
            if now - admitted > deadline:
                decided += 1          # deadline already missed, no need to wait
            else:
                pending += 1
        return successes, decided, pending

    def _capability_state(self, since: float) -> tuple[str, dict | None]:
        """Does the current level meet every type's declared SLO at 95%
        confidence? Returns the same (state, breach) shape as the trend test."""
        target = float(self.cfg["capability_target"])
        floor = int(self.cfg["capability_min_samples"])
        worst: dict | None = None
        state = "good"
        for sid in self.scenario_ids:
            if self.user_scenario and sid not in self.user_scenario:
                continue
            deadline = self._deadline_s(sid)
            if deadline is None:
                return "unconfigured", None
            successes, decided, _pending = self._capability_cohort(sid, since)
            if decided < floor:
                state = "inconclusive"
                continue
            bound = st.wilson_lower(successes, decided)
            if bound < target:
                cand = {"profile": sid, "metric": "capability",
                        "value": round(bound, 4), "limit": target,
                        "observed": round(successes / max(1, decided), 4),
                        "samples": decided, "deadline_s": deadline}
                if worst is None or cand["value"] < worst["value"]:
                    worst = cand
        if worst is not None:
            return "bad", worst
        return state, None

    def _capability_report(self, since: float) -> dict:
        out = {}
        for sid in self.scenario_ids:
            deadline = self._deadline_s(sid)
            if deadline is None:
                continue
            successes, decided, pending = self._capability_cohort(sid, since)
            out[sid] = {"deadline_s": deadline, "decided": decided,
                        "successes": successes, "pending": pending,
                        "observed": round(successes / decided, 4) if decided else None,
                        "lower_bound_95": round(st.wilson_lower(successes, decided), 4)
                        if decided else None}
        return out

    async def _certify_capability(self) -> None:
        """Bracket downward from the level the search phase reached until one
        passes the 95/95 deadline rule, holding each candidate long enough to
        gather the samples the bound requires."""
        if not self.deadlines_configured():
            self.capability_detail = {"status": "not configured"}
            return
        self.phase = "certifying"
        step = self.tile_size or max(1, int(self.cfg["step_users"]))
        attempts = 0
        while len(self.users) >= step and attempts < 8 and not self._stop.is_set():
            attempts += 1
            since = time.time()
            self._rung_t0 = since
            deadline_wait = max(self._deadline_s(sid) or 0 for sid in self.scenario_ids)
            budget = min(600.0, max(60.0, deadline_wait * 3))
            state = "inconclusive"
            while time.time() - since < budget and not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                    if not self.capability_detail:
                        self.capability_detail = {
                            "status": "stopped before certification",
                            "service_class": self.cfg["service_class"],
                            "last_tested_users": len(self.users)}
                    return
                except asyncio.TimeoutError:
                    pass
                state, breach = self._capability_state(since)
                if state in ("good", "bad"):
                    self.breach = breach or self.breach
                    break
            if state == "good":
                self.capability_users = len(self.users)
                if self.tile_size:
                    self.capability_tiles = len(self.users) // self.tile_size
                # A pass on the FIRST candidate bounds rather than measures.
                # Nothing above it was ever put to the deadline, so the real
                # capability may be higher and we have no evidence either way.
                # Only a pass that follows a FAILURE at a higher level has
                # actually bracketed the boundary.
                bounded = attempts == 1
                self.capability_detail = {
                    "status": "lower bound" if bounded else "measured",
                    "service_class": self.cfg["service_class"],
                    "confidence": 0.95, "target": float(self.cfg["capability_target"]),
                    "per_type": self._capability_report(since)}
                if bounded:
                    self.capability_detail["reason"] = (
                        "passed at the first level tested — no higher level was "
                        "put to the deadline")
                return
            self.capability_detail = {
                "status": "not met at any tested level",
                "service_class": self.cfg["service_class"],
                "last_tested_users": len(self.users),
                "per_type": self._capability_report(since)}
            self._remove_users(step)
            await self._drain()


    # ── metric 2: sustainable capacity (open loop, rate-bound) ───────────────

    async def _arrival_loop(self) -> None:
        """Submit work on a schedule that ignores completions.

        A closed loop cannot show queue divergence, because each session holds
        at most one outstanding unit. Here submission timing is fixed, so
        demand above the host's service rate accumulates as backlog and the
        overload becomes directly observable. The queue is bounded: past
        max_backlog the generator records a rejection rather than growing
        without limit, since an out-of-memory kill would end the run before it
        produced evidence.

        Timing: a per-submission sleep of 1/rate cannot be delivered once the
        rate passes a few hundred per second — the event loop's timer
        resolution is coarser than the interval, so the generator would fall
        behind while the run kept reporting the offered rate as fact.
        Submissions are therefore released in batches from a due-counter on a
        fixed 20 ms tick (a late tick releases more, so the average
        self-corrects), and every firing is counted so the ACHIEVED rate is
        recorded per level instead of assumed. After a stall the due-counter
        is clamped to half a second of arrivals: replaying a long gap as one
        burst would hammer the host with a pattern nobody offered, and the
        clamp surfaces in the achieved rate rather than hiding.

        A rejection counts as a delivered arrival — the schedule fired; the
        bounded queue refusing it is the host's problem, not the generator's."""
        TICK = 0.02
        idx = 0
        rotation = self.tile_assignment or self.scenario_ids
        last = time.monotonic()
        due = 0.0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK)
                return
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            rate = max(0.01, self.offered_rate)
            due = min(due + (now - last) * rate, 0.5 * rate)
            last = now
            while due >= 1.0 and not self._stop.is_set():
                due -= 1.0
                self._arrivals += 1
                if len(self._inflight) >= int(self.cfg["max_backlog"]):
                    self.rejected += 1
                else:
                    sid = rotation[idx % len(rotation)]
                    t = asyncio.create_task(self._submit_open(sid, idx))
                    self._open_tasks.add(t)
                    t.add_done_callback(self._open_tasks.discard)
                    # Yield so the task runs to its first await and ADMITS
                    # before the next backlog check — without this a whole
                    # tick's batch lands before _inflight reflects any of it
                    # and the bounded queue overshoots its own bound.
                    await asyncio.sleep(0)
                idx += 1

    async def _submit_open(self, sid: str, idx: int) -> None:
        """One open-loop submission, followed to a terminal outcome."""
        wf = self.scenarios.get(sid) or {}
        self.total_requests += 1
        key = self._admit(sid)
        try:
            if self.mode == "e2e":
                rec = await self._e2e.run_workflow(sid, wf.get("query", ""), {
                    "enabled_tools": wf.get("enabled_tools"),
                    "validator_enabled": wf.get("validator_enabled", True),
                    "budgets": wf.get("budgets"),
                    "toolless": wf.get("toolless", False),
                }, timeout_s=self._profile_timeout_s(sid))
            else:
                steps = wf.get("steps") or []
                rec = await self._caller.call(wf, steps[0] if steps else {},
                                              vary_key=f"{self.seed}:open:{idx}")
        except Exception as exc:  # noqa: BLE001 — a failed unit is a data point
            rec = {"ok": False, "latency_ms": 0.0, "tokens_in": 0, "tokens_out": 0,
                   "error": f"{type(exc).__name__}: {exc}"[:160]}
        t_submit = self._release(key)
        rec.update(scenario=sid, step="workflow", user=-1, ts=time.time(),
                   t_submit=t_submit, offered_rate=self.offered_rate)
        self._check_contract(sid, rec)
        self._tally_call(rec)

    def _tally_call(self, rec: dict) -> None:
        """Append a finished unit and keep the running tallies current."""
        self.calls.append(rec)
        self.completed_requests += 1
        t = self._scen_tally[rec.get("scenario", "?")]
        t["calls"] += 1
        if not rec.get("ok"):
            t["errors"] += 1
            if rec.get("error"):
                t["last_error"] = rec["error"]
        elif rec.get("latency_ms") is not None:
            t["ok_latencies"].append(rec["latency_ms"])

    def _clean_rate(self, since: float) -> float:
        """Clean durable completions per second since `since`.

        Only correct, contract-conforming, durably recorded units count. A host
        cannot raise its apparent capacity by discarding work or by finishing
        it incorrectly."""
        span = max(1e-6, time.time() - since)
        clean = sum(1 for c in self._recent(since)
                    if c.get("ok") and not c.get("invalid")
                    and c.get("durable", True))
        return clean / span

    def _backlog_series(self, since: float) -> tuple[list[float], list[float]]:
        xs, ys = [], []
        for smp in self.samples:
            if smp["ts"] >= since and smp.get("inflight_admitted") is not None:
                xs.append(smp["ts"] - since)
                ys.append(float(smp["inflight_admitted"]))
        return xs, ys

    async def _rate_ramp(self) -> None:
        """Step the offered rate until the backlog diverges or a limit stops us."""
        self.phase = "ramping"
        rate = float(self.cfg["arrival_start_rate"])
        hold = float(self.cfg["arrival_hold_s"])
        diverging = 0
        self._tasks.append(asyncio.create_task(self._arrival_loop()))
        while not self._stop.is_set():
            self.offered_rate = rate
            rejected_at_start = self.rejected
            # Settle before measuring. The first moments at a new rate contain
            # the queue filling to its new steady depth, which reads as growth
            # and has nothing to do with the host's ability to sustain the rate.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=hold / 2)
                return
            except asyncio.TimeoutError:
                pass
            since = time.time()
            arrivals_at_start = self._arrivals
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=hold / 2)
                return
            except asyncio.TimeoutError:
                pass
            span = max(1e-6, time.time() - since)
            achieved = (self._arrivals - arrivals_at_start) / span
            xs, ys = self._backlog_series(since)
            slope_lb = st.slope_lower_bound(xs, ys)
            clean = self._clean_rate(since)
            window = self._recent(since)
            errors = sum(1 for c in window
                         if not c.get("ok") and not c.get("invalid"))
            decided = sum(1 for c in window if not c.get("invalid"))
            # What the generator itself cost during this window, so the
            # in-process contamination is a recorded fact rather than an
            # assumption (measured 0.3% of the host at 21.5 wf/s).
            ctl_cpu = [s_["cpu_by"]["control"] for s_ in self.samples
                       if s_["ts"] >= since and s_.get("cpu_by")
                       and "control" in s_["cpu_by"]]
            level = {"offered_rate": round(rate, 2),
                     # The rate the schedule actually fired at, counted, not
                     # assumed. The gap between offered and achieved is the
                     # generator's own saturation and it must be visible.
                     "achieved_rate": round(achieved, 2),
                     "clean_rate": round(clean, 2),
                     "backlog_slope_lb": round(slope_lb, 4) if slope_lb is not None else None,
                     "outstanding": len(self._inflight),
                     "oldest_inflight_s": self._oldest_inflight_s(),
                     "errors": errors,
                     "err_rate": round(errors / decided, 4) if decided else 0.0,
                     "rejected": self.rejected - rejected_at_start,
                     "control_cpu_pct": (round(statistics.median(ctl_cpu), 1)
                                          if ctl_cpu else None)}
            self.rate_levels.append(level)
            # The +1 grants one arrival at the window boundary: at low rates
            # a short window straddles a period edge, and 9-vs-10 counted
            # arrivals must not read as the generator failing. At real rates
            # one arrival is nothing and the gate keeps its teeth.
            if (self._arrivals - arrivals_at_start + 1) / span < 0.95 * rate:
                # The generator fell behind its own schedule: every conclusion
                # at this level would be about load nobody offered. Stop and
                # censor rather than judging the host on the harness's limit.
                self.verdict = "generator_limit"
                self.breach = {"profile": "harness", "metric": "achieved_rate",
                                "value": round(achieved, 2),
                                "limit": round(0.95 * rate, 2)}
                return
            growing = slope_lb is not None and slope_lb > 0
            failing = (level["err_rate"] > float(self.cfg["error_rate_limit"])
                       or level["rejected"] > 0)
            if growing or failing:
                diverging += 1
                if self.failure_onset is None:
                    self.failure_onset = {
                        "offered_rate": round(rate, 2),
                        "reason": "backlog_growth" if growing else "technical_failure"}
                if diverging >= 2:
                    self.verdict = "queue_divergence" if growing else "errors"
                    self.breach = {"profile": "aggregate",
                                   "metric": "backlog_growth" if growing else "error_rate",
                                   "value": level["backlog_slope_lb"] if growing
                                   else level["err_rate"],
                                   "limit": 0.0 if growing
                                   else float(self.cfg["error_rate_limit"])}
                    return
            else:
                diverging = 0
            if rate >= float(self.cfg["arrival_max_rate"]):
                self.verdict = "capped"
                return
            if (self.cfg.get("max_duration_s") is not None
                    and time.time() - self.started_at > float(self.cfg["max_duration_s"])):
                self.verdict = "timeout"
                return
            rate = min(float(self.cfg["arrival_max_rate"]),
                       rate * float(self.cfg["arrival_step_factor"]))

    def _summarize_capacity(self) -> None:
        """Fit the saturation breakpoint and publish the conservative bound.

        A breakpoint only means something when the offered rate actually
        outran the host. When the run stopped for its OWN reasons — the
        configured rate ceiling, the clock, the spend guard — the throughput
        curve is a straight line, and a segmented fit would happily place a
        knee in the noise at the top of it. That would invent a boundary the
        host never showed. Report the highest rate it sustained instead, as
        the lower bound it is."""
        top_clean = max((lv["clean_rate"] for lv in self.rate_levels), default=None)
        # A hand stop leaves no verdict at all, only the phase, and it censors
        # the run exactly as a ceiling does.
        ended_early = (self.verdict in CENSORING_VERDICTS
                       or (self.verdict is None and self.phase == "stopped"))
        if self.failure_onset is None and ended_early:
            self.capacity_wps = round(top_clean, 2) if top_clean else None
            self.capacity_detail = {
                "status": "lower bound",
                "at_least_workflows_per_s": self.capacity_wps,
                "reason": CENSOR_REASON.get(self.verdict or "stopped", self.verdict),
                "levels": self.rate_levels}
            return
        if len(self.rate_levels) < 4:
            self.capacity_detail = {"status": "too few offered rates to fit a breakpoint"}
            return
        rates = [lv["offered_rate"] for lv in self.rate_levels]
        clean = [lv["clean_rate"] for lv in self.rate_levels]
        fit = st.bootstrap_breakpoint_ci(rates, clean, seed=self.seed or 0)
        if fit is None:
            self.capacity_detail = {"status": "no distinct capacity knee detected",
                                    "levels": self.rate_levels}
            return
        estimate, low, high = fit
        self.capacity_wps = round(low, 2)
        self.capacity_detail = {
            "status": "measured",
            "clean_workflows_per_s": round(low, 2),
            "breakpoint_estimate": round(estimate, 2),
            "ci95": [round(low, 2), round(high, 2)],
            "confirmed_divergence_rate": (self.failure_onset or {}).get("offered_rate"),
            "levels": self.rate_levels}

    # ── result ───────────────────────────────────────────────────────────────
    def _result_kind(self, verdict: str | None) -> str:
        """Classify what this run's numbers mean: see the verdict sets above.

        A run with no number at all is 'inconclusive' regardless of how it
        ended — there is nothing to bound."""
        if verdict in INVALID_VERDICTS:
            return "invalid"
        have_number = (self.capability_users is not None
                       or self.capacity_wps is not None
                       or self.capacity_users is not None)
        if not have_number:
            return "inconclusive"
        if verdict in BOUNDARY_VERDICTS:
            return "boundary"
        if verdict in CENSORING_VERDICTS:
            return "lower_bound"
        return "inconclusive"

    def _finalize(self):
        hold_w = getattr(self, "_hold_window_s", None) or float(self.cfg["hold_s"])
        hold = self._window_stats(hold_w)
        cut = time.time() - hold_w
        hold_samples = [s for s in self.samples if s["ts"] >= cut]

        def avg(key):
            vals = [s[key] for s in hold_samples if s.get(key) is not None]
            return round(statistics.mean(vals), 1) if vals else None

        # Steady-state CPU attribution: who was burning the box at capacity.
        by_samples = [s["cpu_by"] for s in hold_samples if s.get("cpu_by")]
        cpu_breakdown = ({k: round(statistics.mean(b.get(k, 0.0) for b in by_samples), 1)
                          for k in sorted({k for b in by_samples for k in b})}
                         if by_samples else None)
        # Background load is a MEASUREMENT CONDITION, not noise: the CPU
        # saturation verdict is host-level, so other tenants on the box lower
        # the measured capacity. Record it (whole-run median of the 'other'
        # bucket) so two runs under different background load are visibly
        # non-comparable, and the UI can caveat the number.
        bg = [s["cpu_by"]["other"] for s in self.samples
              if s.get("cpu_by") and "other" in s["cpu_by"]]
        background_cpu = round(statistics.median(bg), 1) if bg else None

        per_scenario: dict[str, dict] = {}
        for sid in self.scenario_ids:
            allc = [c for c in self.calls if c["scenario"] == sid]
            cs = [c for c in allc if not c.get("invalid")]
            invalid_n = len(allc) - len(cs)
            ok = [c for c in cs if c["ok"]]
            dur_so_far = max(1e-6, (self.ended_at or time.time()) - self.started_at)
            # ESTIMATE: average tokens concurrently in flight for this profile
            # (token-seconds per second over request lifetimes). This approximates
            # KV pressure during active requests only — whether the engine retains
            # KV/prefix state between requests is engine policy; the measured value
            # is the SGLang KV gauge (kv_pct).
            kv_tok = sum((c["tokens_in"] + c["tokens_out"]) * c["latency_ms"] / 1000.0
                         for c in ok) / dur_so_far
            row = {
                "name": self.scenarios[sid]["name"],
                "users": self.user_scenario.count(sid),
                "calls": len(cs),
                "errors": len(cs) - len(ok),
                "p50_ms": _pct([c["latency_ms"] for c in ok], 50),
                "p95_ms": _pct([c["latency_ms"] for c in ok], 95),
                "tokens_out": sum(c["tokens_out"] for c in ok),
                "avg_tokens_in_flight": round(kv_tok),
            }
            # Failure reason, not just a count — the most recent recorded error
            # is what turns an "errors" verdict from a mystery into a diagnosis.
            if invalid_n:
                row["invalid_units"] = invalid_n
            errs = [c["error"] for c in cs if not c["ok"] and c.get("error")]
            if errs:
                row["last_error"] = errs[-1]
            traces = [c["trace"] for c in ok if c.get("trace")]
            if traces:
                row["trace"] = {
                    k: round(statistics.mean(tr[k] for tr in traces), 1)
                    for k in ("llm_calls", "steps", "validations", "task_count")
                }
            per_scenario[sid] = row

        # Downsample the timeline for the result payload (~120 points max).
        samples = list(self.samples)
        stride = max(1, len(samples) // 120)
        timeline = samples[::stride]

        # Whole-test energy from average power over wall time (best-effort).
        powers = [s["power_w"] for s in samples if s.get("power_w") is not None]
        dur = (self.ended_at or time.time()) - self.started_at
        energy_wh = round(statistics.mean(powers) * dur / 3600, 2) if powers else None

        completed_requests = self.completed_requests
        # How the run ended decides what every number in it means, so classify
        # once here and let the published figures inherit it.
        verdict = self.verdict or ("stopped" if self.phase == "stopped" else None)
        kind = self._result_kind(verdict)
        self.result = {
            "mode": self.mode,
            "benchmark_target": self.benchmark_target,
            "inference_backend": self.inference_backend,
            "verdict": verdict,
            "result_kind": kind,
            "censored": kind == "lower_bound",
            "censor_reason": (CENSOR_REASON.get(verdict or "", verdict)
                              if kind == "lower_bound" else None),
            "phase": self.phase,
            "error": self.error,
            # THE capacity number: the highest level at which the SLO held.
            # Falls back to the held level when the SLO was never breached.
            # THE TWO METRICS. Service capability is a session count against a
            # declared deadline; sustainable capacity is a clean workflow rate
            # against queue divergence. Different questions, different units,
            # never combined into one unlabelled number.
            "capability": {
                "users": self.capability_users,
                "tiles": self.capability_tiles,
                **(self.capability_detail or {}),
            } if (self.capability_detail or self.capability_users) else None,
            "sustainable_capacity": self.capacity_detail or None,
            "capacity_workflows_per_s": self.capacity_wps,
            "failure_onset": self.failure_onset,
            "load_model": str(self.cfg["load_model"]),
            "service_class": str(self.cfg["service_class"]),
            # Diagnostic, closed loop: the level past which added sessions stop
            # being absorbed. It carries no service promise, so it is never the
            # headline and never substitutes for capability.
            "stability_ceiling_users": self.capacity_users,
            "capacity_users": self.capacity_users,
            # Certified means the run found the boundary. A run that ran out
            # of CPU, clock, or dollars first produced a lower bound, and
            # calling that certified sells a harness limit as a system limit.
            "capacity_certified": kind == "boundary" and self.capacity_users is not None,
            "capacity_tiles": (self.capacity_tiles
                                if self.capacity_tiles is not None
                                else (self.capacity_users // self.tile_size
                                      if self.capacity_users and self.tile_size
                                      else None)),
            "mix": self.mix,
            "comparable": self.mix == "tile",
            "tile": self.tile,
            "tile_size": self.tile_size or None,
            "breach": self.breach,
            "knee_users": self.knee_users,
            "slo_capacity_users": self.slo_capacity_users,
            "slo_capacity_tiles": self.slo_capacity_tiles,
            "baseline_p95_ms": self.baseline_p95,
            "baselines": {k: round(v, 1) for k, v in self.baselines.items()},
            "slo": {"p95_x": self.cfg["slo_p95_x"], "p95_ms": self.cfg["slo_p95_ms"],
                     "err": self.cfg["slo_err"], "min_samples": self.cfg["min_samples"]},
            "peak_users": self.peak_users,
            "max_users": self.peak_users,  # compatibility for older exports
            "duration_s": round(dur, 1),
            "total_requests": self.total_requests,
            "completed_requests": completed_requests,
            "unfinished_requests": max(0, self.total_requests - completed_requests),
            "max_in_flight": max((int(s.get("in_flight") or 0) for s in samples),
                                 default=0),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            # Steady-state rate from the certified hold window only. Without a
            # certified capacity there is no honest rate to headline — a
            # whole-run blend of ramp phases and errors is not a rate.
            "workflows_per_hour": (round(hold["rpm"] * 60, 1)
                                     if self.mode == "e2e"
                                     and self.capacity_users is not None
                                     else None),
            "cpu_breakdown": cpu_breakdown,
            "background_cpu_pct": background_cpu,
            "harness": self.harness or None,
            "invalid_units": self.invalid_units,
            "max_inflight_age_s": max((s.get("oldest_inflight_s") or 0
                                       for s in self.samples), default=None),
            "steady": {**hold, "cpu_pct": avg("cpu_pct"), "mem_pct": avg("mem_pct"),
                        "power_w": avg("power_w"), "load1": avg("load1"),
                        "bw_gbs": avg("bw_gbs"), "kv_pct": avg("kv_pct")},
            "mem_mb_per_user": mem_slope_mb_per_user(samples),
            "energy_wh": energy_wh,
            "per_scenario": per_scenario,
            "timeline": timeline,
            "cloud_model": public_endpoint(self.endpoint),
            "pricing": ({"currency": "USD",
                         "input_per_mtok": self.endpoint["input_per_mtok"],
                         "output_per_mtok": self.endpoint["output_per_mtok"],
                         "pricing_as_of": self.endpoint.get("pricing_as_of"),
                         "pricing_url": self.endpoint.get("pricing_url"),
                         "note": self.endpoint.get("pricing_note")}
                        if self.endpoint else None),
            "cost": ({"run_total_usd": round(self.cost_usd, 6),
                      "in_flight_reserved_usd": round(self._reserved_cost_usd, 6),
                      "committed_estimate_usd": round(
                          self.cost_usd + self._reserved_cost_usd, 6),
                      "circuit_breaker_usd": self.cfg.get("max_cost_usd"),
                      "remaining_usd": round(max(0.0, float(self.cfg["max_cost_usd"])
                                                 - self.cost_usd
                                                 - self._reserved_cost_usd), 6),
                      "steady_cost_per_hour": hold.get("cost_per_hour", 0.0),
                      "steady_cost_per_workflow": (round(
                          hold.get("cost_per_hour", 0.0) / (hold["rpm"] * 60), 6)
                          if self.mode == "e2e" and hold["rpm"] else None),
                      "steady_cost_per_1k_requests": (round(
                          hold.get("cost_per_hour", 0.0) / (hold["rpm"] * 60) * 1000, 4)
                          if hold["rpm"] else None)} if self.endpoint else None),
            "capacity_levels": self.capacity_levels,
            "config": {k: self.cfg[k] for k in
                       ("step_interval_s", "cpu_target", "hold_s", "max_cost_usd",
                        "mock_ms", "mock_sigma")},
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "repro": {
                "seed": self.seed,
                "cache_mode": (None if self.mode == "e2e"
                               else self._caller.cache_mode),
                "warmup_s": self.cfg.get("warmup_s"),
                # Host-sharing condition: median CPU from processes OUTSIDE the
                # benchmark. The saturation verdict is host-level, so runs with
                # different background load are not comparable.
                "background_cpu_pct": background_cpu,
                # e2e measurement geometry — derived by declared rule, recorded
                # so a run's certification conditions are reconstructible.
                "eval_window_s": (round(self._eval_window_s, 1)
                                   if self._eval_window_s else None),
                "hold_window_s": (round(getattr(self, "_hold_window_s", 0), 1)
                                   or None),
                "e2e_timeout_s": (float(self.cfg["e2e_timeout_s"])
                                   if self.mode == "e2e" else None),
                "benchmark_version": _scen_version(),
                "scenario_fingerprint": repro_mod.scenario_fingerprint(),
                "git_commit": repro_mod.git_commit(),
                "model": (self._engine_info or {}).get("served_model_name")
                          if self.inference_backend == "local" else
                          (self._backend_model or ((self.endpoint or {}).get("model") or (None
                           if self.inference_backend == "remote_mock"
                           else _remote_model()))),
                "engine": self._engine_info,
                "host": repro_mod.host_info(),
                "mix": self.mix,
                "tile": self.tile,
                "benchmark_target": self.benchmark_target,
                "inference_backend": self.inference_backend,
            },
        }
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.started_at))
            (RESULTS_DIR / f"capacity-{stamp}-{self.mode}.json").write_text(
                json.dumps(self.result, indent=1))
        except OSError:
            pass  # results still available in memory

    async def _persist_db(self):
        """Best-effort: history must never fail a finished test."""
        try:
            from backend.db.base import get_sessionmaker
            from backend.repositories import capacity_runs as caps_repo
            sm = get_sessionmaker()
            async with sm() as session:
                row = await caps_repo.save(session, self.result)
                await session.commit()
            self.result["history_id"] = row.id
        except Exception:  # noqa: BLE001
            pass

    # ── live status for the UI ───────────────────────────────────────────────
    def status(self) -> dict:
        latest = self.samples[-1] if self.samples else {}
        per_scenario = {}
        for sid in self.scenario_ids:
            t = self._scen_tally[sid]
            per_scenario[sid] = {
                "name": self.scenarios[sid]["name"],
                "users": self.user_scenario.count(sid),
                "calls": t["calls"],
                "errors": t["errors"],
                "p50_ms": _pct(list(t["ok_latencies"]), 50),
            }
            if t["last_error"]:
                per_scenario[sid]["last_error"] = t["last_error"]
        samples = list(self.samples)[-150:]
        return {
            "active": self.phase in ("starting", "ramping", "holding"),
            "phase": self.phase,
            "verdict": self.verdict,
            "mode": self.mode,
            "benchmark_target": self.benchmark_target,
            "inference_backend": self.inference_backend,
            "users": len(self.users),
            "capacity_users": self.capacity_users,
            "capacity_tiles": self.capacity_tiles,
            "mix": self.mix,
            "tile_size": self.tile_size or None,
            "breach": self.breach,
            "knee_users": self.knee_users,
            "slo_capacity_users": self.slo_capacity_users,
            "capability_users": self.capability_users,
            "capability_tiles": self.capability_tiles,
            "offered_rate": round(self.offered_rate, 2) or None,
            "capacity_workflows_per_s": self.capacity_wps,
            "rate_levels": self.rate_levels[-12:] or None,
            "invalid_units": self.invalid_units,
            "baseline_p95_ms": self.baseline_p95,
            "elapsed_s": round(time.time() - self.started_at, 1),
            "total_requests": self.total_requests,
            "cost_usd": round(self.cost_usd, 6) if self.endpoint else None,
            "committed_cost_usd": (round(self.cost_usd + self._reserved_cost_usd, 6)
                                    if self.endpoint else None),
            "max_cost_usd": self.cfg.get("max_cost_usd"),
            "cloud_model": public_endpoint(self.endpoint),
            "latest": latest,
            "per_scenario": per_scenario,
            "timeline": samples,
            "error": self.error,
            "result": self.result,
        }
