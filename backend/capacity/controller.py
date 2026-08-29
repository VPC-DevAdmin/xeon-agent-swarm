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
import hashlib
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
                                        descendant_pids, find_pids,
                                        mem_slope_mb_per_user, process_tree_pids,
                                         sample_bandwidth_gbs, sample_kv_pct)
from backend.capacity.client import LOCAL_BASE
from backend.capacity.models import public_endpoint
from backend.capacity import stats as st
from backend.capacity import repro as repro_mod
from backend.capacity import machine_profile as mprofile

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
                                "generator_limit", "weigh_in_timeout"})
INVALID_VERDICTS = frozenset({"workload_invalid", "harness_degraded"})

# Unclassifiable is its own outcome: the host is unfit for every declared
# rung of this workload's ladder. It is a publishable fitness verdict, not a
# bound and not an error, so it belongs to none of the three classes above.

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
    "weigh_in_timeout": "the weigh-in time cap expired before enough "
                        "completions landed to measure the host",
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
    harness_tolerance=0.0,    # publishable runs tolerate no lost durable work
    invalid_tolerance=0.01,   # contract-violating units above this share invalidate the run
    # Capability: the declared SLO metric. A level passes only when the lower
    # one-sided 95% bound on each type's on-deadline success is >= target.
    service_class="interactive",   # legacy label, no longer selects deadlines
    service_rung="auto",           # ladder rung: "auto" = assigned by weigh-in
    capability_target=0.95,
    capability_confidence=0.95,  # joint confidence across all workflow types
    capability_min_samples=0,    # optional operator floor; statistical floor is derived
    # Capacity: the open-loop metric. Offered rate steps upward; a level fails
    # when the lower bound on backlog growth is above zero twice over.
    weigh_in_reuse_days=14.0,    # reuse a machine profile this fresh
    force_weigh_in=False,        # re-measure even when a profile exists
    load_model="closed",         # closed | open
    arrival_hold_s=45.0,
    arrival_step_factor=1.4,
    arrival_refine_utilization=0.95,  # reduce step size as clean rate falls behind arrivals
    arrival_start_rate=2.0,      # overridden by calibration unless disabled
    arrival_calibrated=True,     # aim the rate search at the measured machine
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
        self._good_since: float | None = None        # first good of the current streak
        # Admission registry: work is measured from SUBMISSION, not only from
        # completion. Sampling completions alone hides the slowest and hung
        # units until they time out, so a level can read healthy while it is
        # deteriorating (survivorship bias in the latency distribution).
        self._inflight: dict[int, tuple[str, float]] = {}   # id -> (profile, admitted_at)
        self._admission_rung: dict[int, float] = {}         # id -> rung start at admission
        self._admit_seq = 0
        self.invalid_units = 0                       # units that broke the workload contract
        self.harness: dict = {}                      # persistence/callback integrity counters
        # Capability (closed loop, deadline-bound) and capacity (open loop,
        # rate-bound) are separate numbers in separate units. Neither is
        # derived from the other and neither may be published unlabelled.
        self.capability_users: int | None = None
        self.capability_tiles: int | None = None
        self.capability_detail: dict = {}
        # The service ladder: use-case rungs frozen with the workload. The
        # weigh-in assigns one; everything deadline-shaped reads from it.
        from backend.capacity.scenarios import service_tiers, weigh_in_spec
        self.tiers: list[dict] = service_tiers()
        self.ladder: dict[str, float] = {t["name"]: t["deadline_s"]
                                          for t in self.tiers}
        self.weigh_in_cfg: dict = weigh_in_spec()
        self.assigned_rung: str | None = None
        self.weigh_in: dict = {}
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
        self.arrival_calibration: dict | None = None
        self.failure_onset: dict | None = None
        self._harness_start: dict = {}
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
        self.cancelled_requests = 0
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
        self._router_base_url = e2e_router.get("base_url")
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
            # Harness counters are process-lifetime diagnostics.  Snapshot
            # them before this run so a failure from an earlier benchmark can
            # never contaminate the current result or every child in a set.
            try:
                from backend import workerpool as wp
                self._harness_start = await wp.collect_counters()
            except Exception as exc:  # noqa: BLE001
                self._harness_start = {
                    "snapshot_error": f"{type(exc).__name__}: {exc}"}
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
            self._user_call_n[idx] += 1
            rec: dict | None = None
            try:
                rec = await self._e2e.run_workflow(
                    wid, self._workflow_query(wf, wid, self._user_call_n[idx]), {
                        "enabled_tools": wf.get("enabled_tools"),
                        "validator_enabled": wf.get("validator_enabled", True),
                        "budgets": wf.get("budgets"),
                        "toolless": wf.get("toolless", False),
                    }, timeout_s=self._profile_timeout_s(wid))
            except asyncio.CancelledError:
                self.cancelled_requests += 1
                await self._settle_spend(reserved, {})
                raise
            finally:
                # Cancellation during a scale-back must not strand an
                # admission forever.  The underlying executor may continue,
                # but this session no longer owns an in-flight slot.
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

    def _workflow_query(self, wf: dict, sid: str, sequence: int) -> str:
        """Deterministically vary otherwise identical e2e prompts.

        The suffix is fixed-size and semantically inert, but changes with the
        run seed, workflow type, and sequence.  Real model endpoints therefore
        cannot turn a benchmark into a repeated-prefix cache test, while the
        same seed reproduces the exact same corpus.
        """
        base = str(wf.get("query") or "")
        # Calibration escape hatch: the suffix is under investigation as a
        # planner perturbation on live models. Turning it off is recorded in
        # the run's prompt_corpus so an unsuffixed run can never masquerade
        # as the reference workload.
        if os.getenv("CAPACITY_PROMPT_SUFFIX", "1") == "0":
            return base
        token = hashlib.sha256(
            f"v9:{self.seed}:{sid}:{sequence}".encode()).hexdigest()[:24]
        return f"{base}\n\n[trace-id {token} — not part of the task]"

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
                groups["mock_router"] = descendant_pids(mockrouter._proc.pid)
        except Exception:  # noqa: BLE001
            pass
        if self.inference_backend == "local":
            if self._engine_pids is None:
                # Rescan until the engine's real compute processes are found:
                # they appear after the launcher and rename themselves, so a
                # single early scan catches only the idle parent.
                self._engine_pids = process_tree_pids("sglang") or None
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
            s["oldest_rung_inflight_s"] = self._oldest_inflight_s(
                getattr(self, "_rung_t0", self.started_at))
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
        self._admission_rung[self._admit_seq] = getattr(
            self, "_rung_t0", self.started_at)
        return self._admit_seq

    def _release(self, key: int) -> float:
        """Deregister a finished unit. Returns the time it was admitted."""
        entry = self._inflight.pop(key, None)
        self._admission_rung.pop(key, None)
        return entry[1] if entry else time.time()

    def _oldest_inflight_s(self, admitted_since: float | None = None) -> float | None:
        entries = [t for _sid, t in self._inflight.values()
                   if admitted_since is None or t >= admitted_since]
        if not entries:
            return 0.0
        now = time.time()
        return round(now - min(entries), 2)

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
        if not spec or not rec.get("ok"):
            return
        if not isinstance(trace, dict):
            rec["ok"] = False
            rec["invalid"] = True
            rec["error"] = "contract violation: required trace is missing"
            self.invalid_units += 1
            return
        for field, bounds in spec.items():
            value = trace.get(field)
            if value is None:
                rec["ok"] = False
                rec["invalid"] = True
                rec["error"] = f"contract violation: required field {field} is missing"
                self.invalid_units += 1
                return
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

    def _scenario_window(self, sid: str, window_s: float,
                         admitted_since: float | None = None) -> dict:
        """Per-profile stats over the window: the unit of SLO evaluation."""
        cut = time.time() - window_s
        recent = [c for c in self._recent(cut)
                  if c["scenario"] == sid and not c.get("invalid")
                  and (admitted_since is None
                       or c.get("t_submit", c["ts"]) >= admitted_since)]
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
            s = self._scenario_window(
                sid, window_s, admitted_since=getattr(self, "_rung_t0", None))
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
        # WORK AGING runs before every other gate, on RAW halves. It reads
        # the sampler, not completions, and it exists for the level that has
        # stopped completing work — the level that can never satisfy a
        # completion gate, and whose exploding latencies inflate the margin
        # until the trimmed span goes negative. Nothing downstream may stand
        # between a collapsing level and this check.
        raw_low = max(now - window_s, self._rung_t0)
        raw_span = now - raw_low
        recent_lat = [c["latency_ms"] for c in self._recent(now - window_s)
                      if c.get("ok") and not c.get("invalid")]
        tol = 1.0 + float(self.cfg["drift_tolerance"])
        if raw_span >= 0.5 * window_s:
            raw_mid = raw_low + raw_span / 2.0
            ages1 = [smp.get("oldest_rung_inflight_s",
                             smp.get("oldest_inflight_s"))
                     for smp in self.samples
                     if smp.get("oldest_rung_inflight_s",
                                smp.get("oldest_inflight_s")) is not None
                     and raw_low <= smp["ts"] < raw_mid]
            ages2 = [smp.get("oldest_rung_inflight_s",
                             smp.get("oldest_inflight_s"))
                     for smp in self.samples
                     if smp.get("oldest_rung_inflight_s",
                                smp.get("oldest_inflight_s")) is not None
                     and smp["ts"] >= raw_mid]
            if len(ages1) >= 2 and len(ages2) >= 2:
                a1, a2 = statistics.median(ages1), statistics.median(ages2)
                floor_s = max(2.0, 1.25 * (_pct(recent_lat, 95) or 0) / 1000.0)
                if a1 > 0 and a2 > max(a1 * tol, floor_s):
                    return "bad", {"profile": "aggregate",
                                    "metric": "work_aging",
                                    "value": round(a2, 1),
                                    "limit": round(max(a1 * tol, floor_s), 1),
                                    "baseline_ms": round(a1 * 1000, 1)}

        # ADMISSION MARGIN: only units admitted more than ~1.2x the p95
        # latency ago are eligible for judgment. Without it the young half's
        # tail always sits within one latency of now, its maturity can never
        # clear the gate, and certification deadlocks at any level whose
        # window is proportional to its latency — which is every e2e level by
        # construction. With it, a steady level's young half is ~95% complete
        # by definition, while a climbing level's lingering slow units still
        # hold maturity down until drift or aging condemns.
        margin_s = 1.2 * (_pct(recent_lat, 95) or 0) / 1000.0
        high = now - margin_s
        low = max(now - window_s, self._rung_t0)
        span = high - low
        if span < 0.4 * window_s:
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
        h2, cens2 = _cohort(mid, high)
        need = max(min_n, 2)      # a single-sample quantile is noise, not a trend
        if len(h1) < need or len(h2) < need:
            return "inconclusive", None
        maturity = len(h1) / max(1, len(h1) + cens1)
        if maturity < float(self.cfg["cohort_maturity"]):
            return "inconclusive", None          # slow work has not landed yet
        # The YOUNG half must be mature too, before a GOOD verdict can stand.
        # Its completed units are the fast survivors of whatever was admitted
        # there, and on a level whose latency is climbing that survivor bias
        # reads a drifting distribution as flat — the ramp then advances on
        # the first misread and the level is never judged again. A BAD verdict
        # may still fire from an immature young half, because survivors
        # reading bad means the truth is at least that bad.
        maturity2 = len(h2) / max(1, len(h2) + cens2)
        h2_mature = maturity2 >= float(self.cfg["cohort_maturity"])

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
        if not h2_mature:
            return "inconclusive", None          # survivors look flat; wait
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
        # Rung eligibility is measured HERE, at one tile with the machine
        # otherwise idle, because the weigh-in is a fitness statement about
        # the host at its best, not under load.
        if self.mode == "e2e" and self.deadlines_configured():
            if not await self._weigh_in():
                return
            self.phase = "ramping"
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
                            3.0 * _pct(rung1_ok, 95) if rung1_ok else 0)
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
                # could not produce min_samples. Starvation condemns only a
                # profile that is IDLE — no unit of its in flight. A profile
                # whose unit is still running is slow, not absent: at one
                # session per type with minutes-long workflows, one straggler
                # can empty a whole window, and condemning that reads a
                # measurement-geometry artifact as instability. Slow-but-alive
                # dwells, and a true stall is the work-aging rule's job.
                inflight_types = {p for p, _t in self._inflight.values()}
                starved = next(
                    (sid for sid in self.scenario_ids
                     if (not self.user_scenario or sid in self.user_scenario)
                     and sid not in inflight_types
                     and self._scenario_window(
                         sid, eval_window, admitted_since=self._rung_t0)["n"]
                     < int(self.cfg["min_samples"])), None)
                if starved is None:
                    pass          # every short profile is in flight: dwell
                else:
                    rung_state = "bad"
                    breach = {"profile": starved, "metric": "no_samples",
                              "value": 0.0,
                              "limit": float(self.cfg["min_samples"])}
            if rung_state == "good":
                # CONFIRMATION IS SYMMETRIC. Condemnation needs two bads a
                # window apart; a single instant good let a level certify off
                # its first window before a fresh climb was visible. A good
                # now certifies only when a second good lands at least half a
                # window after the first. An inconclusive in between is NO
                # INFORMATION and keeps the streak — exactly as it keeps the
                # bad record — and only a bad or a level change resets it.
                # Confirmation applies to TILE mode, where levels are
                # discrete certified plateaus. A custom mix adds a session
                # every interval, so no confirmation window can fit between
                # advances by construction — and custom mixes are declared
                # non-comparable diagnostics, so the rigor lives where the
                # comparability claim lives.
                now_g = time.time()
                if self._good_since is None:
                    self._good_since = now_g
                confirmed = (self.mix != "tile"
                             or now_g - self._good_since >= 0.5 * eval_window)
                self._rung_confirmed = confirmed
                if at_boundary and confirmed:
                    self.capacity_users = len(self.users)
                    if self.mix == "tile":
                        self.capacity_tiles = len(self.users) // max(1, self.tile_size)
                    self._update_slo_overlay(eval_window)
                slo_bad = 0
                # A transient bad that a later evaluation supersedes was a
                # blip, not the boundary: breach describes what ENDED the run.
                self.breach = None
            elif rung_state == "bad":
                self._good_since = None
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
            # An inconclusive reading is no information and keeps the good
            # streak, exactly as it keeps the bad record. Only a bad reading
            # or a level change contradicts a good.
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
            if at_boundary and (rung_state != "good"
                                or not getattr(self, "_rung_confirmed", True)):
                # Advance and certification share ONE gate: a level that has
                # not confirmed its good streak has not certified, and the
                # ramp never climbs off an unconfirmed level.
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
            self._good_since = None       # a new level earns its own streak

    def _current_eval_window(self, interval: float) -> float:
        """e2e SLO window sized to what workflows take NOW, at THIS level.

        3x the median, from the CURRENT rung's own completions. Both choices
        are load-bearing. A global recent-completions median lags a level
        transition, and a window sized by the previous faster era can fall
        below the current latency, where no unit can be admitted and finish
        inside one window and the cohort is unjudgeable forever. And 2x was
        knife-edge by construction: the older half-window then equals one
        median latency, so only faster-than-median units ever land in it,
        starving the very cohort test the window exists to feed. 3x gives the
        older half 1.5 medians of room."""
        if self.mode != "e2e":
            return 3 * interval
        import itertools
        rung_t0 = getattr(self, "_rung_t0", self.started_at)
        rung = [c["latency_ms"] / 1000.0
                for c in itertools.islice(reversed(self.calls), 400)
                if c["ok"] and c.get("t_submit", c["ts"]) >= rung_t0]
        if len(rung) < 3:
            lats = [c["latency_ms"] / 1000.0
                    for c in itertools.islice(reversed(self.calls), 200)
                    if c["ok"]]
            rung = lats or rung
        if not rung:
            return self._eval_window_s or 3 * interval
        # p95, not median: the admission margin is 1.2x p95, and on a level
        # whose latency is climbing the median lags at roughly half the
        # front while p95 tracks it — a median-based window then grows
        # slower than the margin, the trimmed span collapses, and drift
        # starves exactly when it is needed. Sizing window and margin in
        # the same statistic keeps the judgeable span open at any slope.
        win = max(3 * interval, 3.0 * (_pct(rung, 95) or 0))
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
               and len(self._inflight) > len(self.users)):
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
            self.verdict = "harness_degraded"
            self.breach = {"profile": "harness", "metric": "counter_collection",
                           "value": 1, "limit": 0}
            return
        baseline = self._harness_start or {}
        raw = dict(counters)
        for name in ("persist_failures", "callback_failures"):
            counters[name] = max(0, int(raw.get(name, 0))
                                 - int(baseline.get(name, 0)))
        counters["counter_baseline"] = baseline
        total = max(1, self.total_requests)
        lost = int(counters.get("persist_failures", 0)) + \
            int(counters.get("callback_failures", 0))
        unreachable = int(raw.get("unreachable_executors", 0))
        counters["lost_fraction"] = round(lost / total, 5)
        counters["invalid_units"] = self.invalid_units
        counters["ok"] = (not baseline.get("snapshot_error")
                          and unreachable == 0
                          and lost <= total * float(self.cfg["harness_tolerance"]))
        self.harness = counters
        if self.invalid_units > self.total_requests * float(self.cfg["invalid_tolerance"]):
            self.verdict = "workload_invalid"
            self.breach = {"profile": "workload", "metric": "contract_violations",
                           "value": self.invalid_units,
                           "limit": round(self.total_requests
                                          * float(self.cfg["invalid_tolerance"]), 1)}
        if not counters["ok"]:
            self.verdict = "harness_degraded"
            if unreachable:
                self.breach = {"profile": "harness",
                               "metric": "unreachable_executors",
                               "value": unreachable, "limit": 0}
            elif baseline.get("snapshot_error"):
                self.breach = {"profile": "harness",
                               "metric": "counter_snapshot", "value": 1,
                               "limit": 0}
            else:
                self.breach = {"profile": "harness", "metric": "lost_records",
                               "value": lost,
                               "limit": round(total * float(
                                   self.cfg["harness_tolerance"]), 1)}


    # ── metric 1: service capability (closed loop, deadline-bound) ───────────

    def _deadline_s(self, sid: str) -> float | None:
        """The assigned rung's deadline, in seconds.

        Rungs are use-case policy declared in the workload ladder. The
        weigh-in decides WHICH rung this host is judged on; it never invents
        a deadline, because the ladder is finite and a host whose weigh-in
        fits no rung is unclassifiable rather than granted an easier target.
        The reference tile's workflow types are same-size units, so one rung
        deadline applies to every type."""
        del sid
        if self.assigned_rung is None:
            return None
        value = self.ladder.get(self.assigned_rung)
        return float(value) if value else None

    def deadlines_configured(self) -> bool:
        return bool(self.ladder)

    def _rung_overlays(self, since: float) -> dict:
        """Observed on-deadline bounds for EVERY ladder rung at this level.

        The certified claim belongs to the assigned rung alone. The overlays
        let a reader with a different responsiveness need see, from the same
        cohort, how this level would have scored on their rung — labelled
        observed, never certified."""
        out: dict = {}
        for rung, deadline in self.ladder.items():
            per_type: dict = {}
            for sid in self.scenario_ids:
                if self.user_scenario and sid not in self.user_scenario:
                    continue
                successes, decided, pending = self._capability_cohort(
                    sid, since, deadline_s=deadline)
                per_type[sid] = {
                    "decided": decided, "successes": successes,
                    "pending": pending,
                    "observed": (round(successes / decided, 4)
                                  if decided else None)}
            out[rung] = {"deadline_s": deadline, "per_type": per_type,
                         "certified": rung == self.assigned_rung}
        return out

    def _capability_cohort(self, sid: str, since: float, *,
                           deadline_s: float | None = None
                           ) -> tuple[int, int, int]:
        """(successes, decided, pending) for one type at the current level.

        A cohort member is decided when it finished or when its deadline has
        already passed. A unit still running inside its deadline is pending and
        counts as neither, which is what keeps a slow level from looking clean
        simply because its slow work has not landed yet."""
        deadline = deadline_s if deadline_s is not None else self._deadline_s(sid)
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
        active = [sid for sid in self.scenario_ids
                  if not self.user_scenario or sid in self.user_scenario]
        z = st.familywise_z(
            len(active), float(self.cfg["capability_confidence"]))
        floor = max(int(self.cfg["capability_min_samples"]),
                    st.samples_for_bound(target, z))
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
            bound = st.wilson_lower(successes, decided, z)
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
        active = [sid for sid in self.scenario_ids
                  if not self.user_scenario or sid in self.user_scenario]
        z = st.familywise_z(
            len(active), float(self.cfg["capability_confidence"]))
        for sid in self.scenario_ids:
            deadline = self._deadline_s(sid)
            if deadline is None:
                continue
            successes, decided, pending = self._capability_cohort(sid, since)
            lower = (round(st.wilson_lower(successes, decided, z), 4)
                     if decided else None)
            out[sid] = {"deadline_s": deadline, "decided": decided,
                        "successes": successes, "pending": pending,
                        "observed": round(successes / decided, 4) if decided else None,
                        "lower_bound_95": lower,
                        "lower_bound_joint_95": lower}
        return out

    async def _set_capability_users(self, target: int) -> None:
        """Move to an exact candidate level, preserving the reference mix."""
        step = self.tile_size or max(1, int(self.cfg["step_users"]))
        target = max(step, (int(target) // step) * step)
        if len(self.users) > target:
            self._remove_users(len(self.users) - target)
            await self._drain()
        while len(self.users) < target and not self._stop.is_set():
            if self.mix == "tile" and self.tile_size:
                if not self._add_tile():
                    break
            else:
                self._add_user()

    async def _measure_capability_candidate(self) -> tuple[str, dict | None, float]:
        """Hold one candidate until it passes, fails, or remains inconclusive."""
        since = time.time()
        self._rung_t0 = since
        deadline_wait = max(self._deadline_s(sid) or 0 for sid in self.scenario_ids)
        budget = max(60.0, deadline_wait * 3,
                     float(self.cfg["step_interval_s"]) * 3)
        if self.cfg.get("max_duration_s") is not None:
            remaining = (float(self.cfg["max_duration_s"])
                         - (time.time() - self.started_at))
            if remaining <= 0:
                return "inconclusive", None, since
            budget = min(budget, remaining)
        state, breach = "inconclusive", None
        poll_s = min(5.0, max(0.1, float(self.cfg["step_interval_s"])))
        while time.time() - since < budget and not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll_s)
                break
            except asyncio.TimeoutError:
                pass
            state, breach = self._capability_state(since)
            if state in ("good", "bad", "unconfigured"):
                break
        return state, breach, since

    def _machine_fingerprint(self) -> str:
        return mprofile.fingerprint(
            benchmark_target=self.benchmark_target,
            inference_backend=self.inference_backend,
            benchmark_version=_scen_version(),
            model=(self._engine_info or {}).get("served_model_name")
                  or self._backend_model,
            engine=self._engine_info,
            host=repro_mod.host_info())

    async def _weigh_in(self) -> bool:
        """Place this machine in a deadline tier.

        The weigh-in exists to set a REASONABLE agent deadline for the
        machine under test, not to gate it. The protocol is frozen with the
        workload: at one tile, wait for the declared number of completions
        per type, take each type's median, and place the machine in the
        first tier whose median ceiling covers the WORST type's median. The
        worst type decides because one fast workflow must not carry a slow
        one into a deadline it cannot meet. The last tier has no ceiling, so
        every machine lands somewhere and none is ever excluded. Only the
        time cap expiring without enough completions stops a run here, and
        that is an evidence statement rather than a category judgment.

        An operator override (service_rung != auto) skips the wait but is
        recorded as an override, so an overridden certification can never
        pass as an earned one."""
        # A machine's speed is a property of the MACHINE. If this exact
        # configuration has been characterized recently, reuse it: the tier
        # comes from POOLED observations (steadier than any single draw) and
        # the run records that it reused rather than measured. Change the
        # workload, model, engine geometry, or hardware and the fingerprint
        # changes with it, so a stale profile cannot follow a machine that is
        # no longer the same machine.
        fp = self._machine_fingerprint()
        if (str(self.cfg.get("service_rung") or "auto") == "auto"
                and not bool(self.cfg.get("force_weigh_in"))):
            cached = mprofile.lookup(fp, ttl_days=float(
                self.cfg.get("weigh_in_reuse_days") or
                mprofile.DEFAULT_TTL_DAYS))
            if cached and cached.get("tier") in self.ladder:
                self.assigned_rung = cached["tier"]
                self.weigh_in = {
                    "protocol": (f"reused machine profile, pooled median of "
                                 f"{cached['observation_count']} weigh-ins"),
                    "source": "machine_profile", "override": False,
                    "fingerprint": fp,
                    "profile_age_days": cached.get("age_days"),
                    "observation_count": cached.get("observation_count"),
                    "worst_median_s": cached.get("pooled_worst_median_s"),
                    "pooled_worst_median_s": cached.get("pooled_worst_median_s"),
                    "observed_range_s": cached.get("observed_range_s"),
                    "tier": cached["tier"],
                    "rung": cached["tier"],
                    "deadline_s": self.ladder[cached["tier"]]}
                logging.getLogger(__name__).info(
                    "weigh-in reused: %s (pooled %.1fs, %d obs)",
                    cached["tier"], cached.get("pooled_worst_median_s") or 0,
                    cached.get("observation_count") or 0)
                return True

        requested = str(self.cfg.get("service_rung") or "auto")
        if requested != "auto":
            if requested not in self.ladder:
                self.error = f"unknown service tier: {requested}"
                self.phase = "error"
                return False
            self.assigned_rung = requested
            self.weigh_in = {"protocol": "operator override",
                             "override": True, "rung": requested,
                             "deadline_s": self.ladder[requested]}
            return True
        spec = self.weigh_in_cfg
        need = int(spec["samples_per_type"])
        deadline_cap = time.time() + float(spec["max_s"])
        self.phase = "weigh_in"
        active = [sid for sid in self.scenario_ids
                  if not self.user_scenario or sid in self.user_scenario]
        while time.time() < deadline_cap and not self._stop.is_set():
            counts = {sid: [] for sid in active}
            for c in self.calls:
                if c.get("ok") and not c.get("invalid") and c["scenario"] in counts:
                    counts[c["scenario"]].append(c["latency_ms"] / 1000.0)
            if all(len(v) >= need for v in counts.values()):
                medians = {sid: round(statistics.median(v), 1)
                           for sid, v in counts.items()}
                worst = max(medians.values())
                # The worst type's median places the machine in the first
                # tier whose ceiling covers it. The last tier has no ceiling,
                # so every machine lands somewhere.
                tier = next((t for t in self.tiers
                             if t.get("max_median_s") is None
                             or worst <= t["max_median_s"]), None)
                if tier is None:
                    self.error = "no service tiers configured"
                    self.phase = "error"
                    return False
                # Record the observation and re-place from POOLED data, so
                # every future run on this machine starts from a steadier
                # characterization than this single draw.
                entry = mprofile.record(fp, medians, tiers=self.tiers,
                                        commit=repro_mod.git_commit())
                pooled_tier = entry.get("tier") or tier["name"]
                self.weigh_in = {
                    "protocol": (f"worst-type median of {need} completions "
                                 f"per type at one tile"),
                    "source": "measured", "override": False,
                    "fingerprint": fp,
                    "medians_s": medians, "worst_median_s": worst,
                    "this_draw_tier": tier["name"],
                    "pooled_worst_median_s": entry.get("pooled_worst_median_s"),
                    "observation_count": entry.get("observation_count"),
                    "observed_range_s": entry.get("observed_range_s"),
                    "tier": pooled_tier,
                    "tier_ceiling_s": next(
                        (t.get("max_median_s") for t in self.tiers
                         if t["name"] == pooled_tier), None),
                    "rung": pooled_tier,
                    "deadline_s": self.ladder[pooled_tier]}
                self.assigned_rung = pooled_tier
                return True
            # Poll at the run's own cadence: a coarse fixed poll silently
            # taxes short duration budgets on fast workloads.
            poll = min(2.0, max(0.2, float(self.cfg["step_interval_s"]) / 2))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll)
                return False
            except asyncio.TimeoutError:
                pass
        if not self._stop.is_set():
            # Cap expiry means the host produced too few completions to weigh
            # at all — an evidence statement, not a category exclusion.
            self.verdict = "weigh_in_timeout"
            self.breach = {"profile": "aggregate", "metric": "weigh_in_timeout",
                           "value": round(float(spec["max_s"]), 1), "limit": 0}
            self.weigh_in = {"protocol": "expired before enough completions",
                             "override": False, "rung": None}
        return False

    async def _certify_capability(self) -> None:
        """Bracket capability with exponential descent and tile refinement.

        A statistically inconclusive level is never treated as a failure and
        therefore never supplies the upper side of a measured boundary.
        """
        if self.mode != "e2e" or not self.deadlines_configured():
            self.capability_detail = {"status": "not configured"}
            return
        if self.assigned_rung is None:
            self.capability_detail = {"status": "no rung assigned",
                                      "weigh_in": self.weigh_in or None}
            return
        self.phase = "certifying"
        step = self.tile_size or max(1, int(self.cfg["step_users"]))
        failed_level: int | None = None
        inconclusive_seen = False
        descent = step
        last_since = time.time()

        while len(self.users) >= step and not self._stop.is_set():
            state, breach, last_since = await self._measure_capability_candidate()
            current = len(self.users)
            if state == "good":
                passed_level = current
                passed_since = last_since
                # Once a true failure and a pass bracket the boundary, refine
                # to one tile.  This may add sessions back; every candidate is
                # held over a fresh admission cohort.
                while (failed_level is not None
                       and failed_level - passed_level > step
                       and not self._stop.is_set()):
                    slots = (failed_level - passed_level) // step
                    candidate = passed_level + max(1, slots // 2) * step
                    await self._set_capability_users(candidate)
                    rstate, rbreach, rsince = await self._measure_capability_candidate()
                    last_since = rsince
                    if rstate == "good":
                        passed_level = candidate
                        passed_since = rsince
                    elif rstate == "bad":
                        failed_level = candidate
                        self.breach = rbreach or self.breach
                    else:
                        inconclusive_seen = True
                        break
                await self._set_capability_users(passed_level)
                self.capability_users = passed_level
                if self.tile_size:
                    self.capability_tiles = passed_level // self.tile_size
                measured = (failed_level is not None
                            and failed_level - passed_level == step
                            and not inconclusive_seen)
                self.capability_detail = {
                    "status": "measured" if measured else "lower bound",
                    "definitive": True,
                    "rung": self.assigned_rung,
                    "deadline_s": self.ladder.get(self.assigned_rung),
                    "weigh_in": self.weigh_in or None,
                    "rung_overlays": self._rung_overlays(passed_since),
                    "service_class": self.cfg["service_class"],
                    "confidence": float(self.cfg["capability_confidence"]),
                    "confidence_scope": "joint across workflow types",
                    "target": float(self.cfg["capability_target"]),
                    "next_failed_users": failed_level if measured else None,
                    "per_type": self._capability_report(passed_since)}
                if not measured:
                    self.capability_detail["reason"] = (
                        "no statistically failed adjacent level was established"
                        if inconclusive_seen else
                        "passed at the first level tested — no higher level was put to the deadline")
                return
            if state == "bad":
                failed_level = current
                self.breach = breach or self.breach
            else:
                inconclusive_seen = True
            if current <= step:
                break
            target = max(step, current - descent)
            await self._set_capability_users(target)
            descent *= 2

        # A run that ends without a pass carries one of THREE distinct
        # findings, and the difference is what a reader is allowed to
        # conclude. A level that FAILED with mature evidence is a measured
        # negative. A run where no level could gather the required cohort is
        # EVIDENCE-LIMITED: the host's ceiling sits below the minimum
        # conclusive cohort, and saying "not met" there would publish an
        # outcome predetermined by our own sample economics rather than
        # measured. The benchmark states that constraint itself, with the
        # floor and observed rates shown, so no reviewer discovers it for us.
        # Both findings are DEFINITIVE for repeat sets; a hand-stop is not.
        active_types = [sid for sid in self.scenario_ids
                        if not self.user_scenario or sid in self.user_scenario]
        floor = max(int(self.cfg["capability_min_samples"]),
                    st.samples_for_bound(
                        float(self.cfg["capability_target"]),
                        st.familywise_z(len(active_types),
                                        float(self.cfg["capability_confidence"]))))
        if self._stop.is_set():
            status, definitive = "stopped before certification", False
        elif failed_level is not None:
            status, definitive = "not met at tested levels", True
        else:
            status, definitive = ("evidence limited: host ceiling below the "
                                  "conclusive cohort"), True
        self.capability_detail = {
            "status": status,
            "definitive": definitive,
            "rung": self.assigned_rung,
            "deadline_s": self.ladder.get(self.assigned_rung),
            "weigh_in": self.weigh_in or None,
            "service_class": self.cfg["service_class"],
            "last_tested_users": len(self.users),
            "highest_failed_users": failed_level,
            "required_samples_per_type": floor,
            "inconclusive_seen": inconclusive_seen,
            "per_type": self._capability_report(last_since)}


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
        if self.mode == "e2e":
            token_envelope = int((wf.get("budgets") or {}).get("max_total_tokens")
                                 or 50_000)
            estimated_in, estimated_out = (int(token_envelope * 0.8),
                                            int(token_envelope * 0.2))
        else:
            step = (wf.get("steps") or [{}])[0]
            estimated_in = int(step.get("prompt_tokens") or 0)
            estimated_out = int(step.get("max_tokens") or 0)
        reserved = await self._reserve_spend(estimated_in, estimated_out)
        if reserved is None:
            return
        self.total_requests += 1
        key = self._admit(sid)
        try:
            if self.mode == "e2e":
                rec = await self._e2e.run_workflow(
                    sid, self._workflow_query(wf, sid, idx), {
                    "enabled_tools": wf.get("enabled_tools"),
                    "validator_enabled": wf.get("validator_enabled", True),
                    "budgets": wf.get("budgets"),
                    "toolless": wf.get("toolless", False),
                }, timeout_s=self._profile_timeout_s(sid))
            else:
                steps = wf.get("steps") or []
                rec = await self._caller.call(wf, steps[0] if steps else {},
                                              vary_key=f"{self.seed}:open:{idx}")
        except asyncio.CancelledError:
            self.cancelled_requests += 1
            self._release(key)
            await self._settle_spend(reserved, {})
            raise
        except Exception as exc:  # noqa: BLE001 — a failed unit is a data point
            rec = {"ok": False, "latency_ms": 0.0, "tokens_in": 0, "tokens_out": 0,
                   "error": f"{type(exc).__name__}: {exc}"[:160]}
        t_submit = self._release(key)
        rec.update(scenario=sid, step="workflow", user=-1, ts=time.time(),
                   t_submit=t_submit, offered_rate=self.offered_rate)
        self._check_contract(sid, rec)
        self._tally_call(rec)
        await self._settle_spend(reserved, rec)

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

    def _resource_observation(self, since: float) -> tuple[str | None, dict | None]:
        """Classify one open-loop measurement window's host resources."""
        rows = [s for s in self.samples if s["ts"] >= since]
        def mean(key: str) -> float | None:
            vals = [float(s[key]) for s in rows if s.get(key) is not None]
            return statistics.mean(vals) if vals else None
        cpu, mem, kv = mean("cpu_pct"), mean("mem_pct"), mean("kv_pct")
        bg = [float(s["cpu_by"]["other"]) for s in rows
              if s.get("cpu_by") and s["cpu_by"].get("other") is not None]
        background = statistics.mean(bg) if bg else None
        host_is_target = (self.benchmark_target != "inference_engine"
                          or self.inference_backend == "local")
        if host_is_target and cpu is not None and cpu >= float(self.cfg["cpu_target"]):
            if background is not None and background >= 0.5 * cpu:
                return "interference", {
                    "profile": "host", "metric": "background_cpu",
                    "value": round(background, 1), "limit": round(0.5 * cpu, 1)}
            return "cpu", {"profile": "host", "metric": "cpu_pct",
                           "value": round(cpu, 1),
                           "limit": float(self.cfg["cpu_target"])}
        if host_is_target and mem is not None and mem >= float(self.cfg["mem_target"]):
            return "memory", {"profile": "host", "metric": "mem_pct",
                              "value": round(mem, 1),
                              "limit": float(self.cfg["mem_target"])}
        if (self.inference_backend == "local" and kv is not None
                and kv >= float(self.cfg["kv_target"])):
            return "kv", {"profile": "engine", "metric": "kv_pct",
                          "value": round(kv, 1),
                          "limit": float(self.cfg["kv_target"])}
        return None, None

    def _open_window_geometry(self) -> tuple[float, float]:
        """Settling and measurement periods scaled to observed workflow time."""
        base = float(self.cfg["arrival_hold_s"])
        recent = [c["latency_ms"] / 1000.0 for c in self._recent(time.time() - 2 * base)
                  if c.get("ok") and c.get("latency_ms") is not None]
        median_s = statistics.median(recent) if recent else 0.0
        return max(base / 2.0, median_s), max(base, 2.0 * median_s)

    async def _measure_open_level(self, rate: float, *, confirm: bool = False
                                  ) -> dict | None:
        """Measure one disjoint window at a fixed achieved arrival rate."""
        self.offered_rate = rate
        settle, measure = self._open_window_geometry()
        if not confirm:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settle)
                return None
            except asyncio.TimeoutError:
                pass
        since = time.time()
        arrivals_at_start = self._arrivals
        rejected_at_start = self.rejected
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=measure)
            return None
        except asyncio.TimeoutError:
            pass
        span = max(1e-6, time.time() - since)
        arrivals = self._arrivals - arrivals_at_start
        achieved = arrivals / span
        xs, ys = self._backlog_series(since)
        slope_lb = st.queue_growth_lower_bound(
            xs, ys, seed=(self.seed or 0) + len(self.rate_levels))
        clean = self._clean_rate(since)
        window = self._recent(since)
        errors = sum(1 for c in window
                     if not c.get("ok") and not c.get("invalid"))
        decided = sum(1 for c in window if not c.get("invalid"))
        per_type = {}
        for sid in self.scenario_ids:
            typed = [c for c in window if c.get("scenario") == sid
                     and not c.get("invalid")]
            typed_errors = sum(1 for c in typed if not c.get("ok"))
            per_type[sid] = {
                "decided": len(typed), "errors": typed_errors,
                "err_rate": round(typed_errors / len(typed), 4) if typed else 0.0}
        worst_type = max(per_type, key=lambda sid: per_type[sid]["err_rate"],
                         default=None)
        ctl_cpu = [s_["cpu_by"]["control"] for s_ in self.samples
                   if s_["ts"] >= since and s_.get("cpu_by")
                   and "control" in s_["cpu_by"]]
        resource_verdict, resource_breach = self._resource_observation(since)
        return {
            "window": "confirmation" if confirm else "measurement",
            "window_s": round(span, 2),
            "offered_rate": round(rate, 2),
            "achieved_rate": round(achieved, 2),
            "clean_rate": round(clean, 2),
            "backlog_slope_lb": round(slope_lb, 4) if slope_lb is not None else None,
            "outstanding": len(self._inflight),
            "oldest_inflight_s": self._oldest_inflight_s(),
            "errors": errors,
            "err_rate": round(errors / decided, 4) if decided else 0.0,
            "per_type": per_type,
            "worst_error_type": worst_type,
            "rejected": self.rejected - rejected_at_start,
            "control_cpu_pct": (round(statistics.median(ctl_cpu), 1)
                                  if ctl_cpu else None),
            "generator_ok": (arrivals + 1) / span >= 0.95 * rate,
            "resource_verdict": resource_verdict,
            "resource_breach": resource_breach,
        }

    def _calibrate_arrival_schedule(self) -> dict | None:
        """Point the rate search at this machine's actual service rate.

        The shipped schedule (2/s to 4000/s) was set for mock-backed runs.
        A CPU-inference node services ~0.011 workflows/second, so that
        schedule opens 180x above the machine's drain rate: every level
        diverges instantly and the breakpoint fit has no points beneath the
        knee to fit against.

        Deriving the SEARCH RANGE from a known machine speed is not the
        circularity the deadlines rule forbids. A deadline is the grading
        bar, so taking it from the box under test corrupts the grade. An
        arrival schedule only decides where to look — like the starting
        bounds of a binary search, it changes how fast the knee is found,
        never where the knee is. The calibration and its basis are recorded
        with the result so a reader can see the search was aimed, not tuned.
        """
        if not self.cfg.get("arrival_calibrated", True):
            return None
        median_s = None
        wi = self.weigh_in or {}
        for key in ("pooled_worst_median_s", "worst_median_s"):
            if wi.get(key):
                median_s = float(wi[key])
                break
        if not median_s:
            lats = [c["latency_ms"] / 1000.0 for c in self.calls if c.get("ok")]
            median_s = statistics.median(lats) if lats else None
        if not median_s or median_s <= 0:
            return None
        # One tile's throughput is the anchor: sessions divided by how long
        # each takes. Open below it so the fit gets points on the
        # proportional segment, and cap far above so a machine that scales
        # past one tile is not capped before its own knee.
        tile = max(1, self.tile_size or len(self.users) or 1)
        service_rate = tile / median_s
        return {"start_rate": max(1e-4, round(0.25 * service_rate, 5)),
                "max_rate": round(20.0 * service_rate, 4),
                "basis": (f"one tile ({tile} sessions) at a {median_s:.0f}s "
                          f"median = {service_rate:.4f} workflows/s"),
                "estimated_service_rate": round(service_rate, 5)}

    async def _rate_ramp(self) -> None:
        """Step the offered rate until the backlog diverges or a limit stops us."""
        self.phase = "ramping"
        cal = self._calibrate_arrival_schedule()
        if cal:
            self.cfg["arrival_start_rate"] = cal["start_rate"]
            self.cfg["arrival_max_rate"] = cal["max_rate"]
            self.arrival_calibration = cal
            logging.getLogger(__name__).info(
                "arrival schedule calibrated: %.5f/s to %.4f/s (%s)",
                cal["start_rate"], cal["max_rate"], cal["basis"])
        rate = float(self.cfg["arrival_start_rate"])
        resource_streak: dict[str, int] = defaultdict(int)
        self._tasks.append(asyncio.create_task(self._arrival_loop()))
        while not self._stop.is_set():
            level = await self._measure_open_level(rate)
            if level is None:
                return
            self.rate_levels.append(level)
            if not level["generator_ok"]:
                self.verdict = "generator_limit"
                self.breach = {"profile": "harness", "metric": "achieved_rate",
                                "value": level["achieved_rate"],
                                "limit": round(0.95 * rate, 2)}
                return

            rv = level.get("resource_verdict")
            for name in ("cpu", "memory", "kv", "interference"):
                resource_streak[name] = resource_streak[name] + 1 if rv == name else 0
            if rv and resource_streak[rv] >= 2:
                self.verdict, self.breach = rv, level.get("resource_breach")
                return

            growing = (level["backlog_slope_lb"] is not None
                       and level["backlog_slope_lb"] > 0)
            worst = level.get("worst_error_type")
            type_failing = bool(worst and level["per_type"][worst]["decided"]
                                and level["per_type"][worst]["err_rate"]
                                > float(self.cfg["slo_err"]))
            failing = (level["err_rate"] > float(self.cfg["error_rate_limit"])
                       or type_failing or level["rejected"] > 0)
            if growing or failing:
                # Confirmation is a second DISJOINT window at this exact same
                # offered rate.  A failure at the next geometric step is not
                # confirmation of the previous rate's instability.
                confirm = await self._measure_open_level(rate, confirm=True)
                if confirm is None:
                    return
                self.rate_levels.append(confirm)
                if not confirm["generator_ok"]:
                    self.verdict = "generator_limit"
                    self.breach = {"profile": "harness", "metric": "achieved_rate",
                                   "value": confirm["achieved_rate"],
                                   "limit": round(0.95 * rate, 2)}
                    return
                crv = confirm.get("resource_verdict")
                for name in ("cpu", "memory", "kv", "interference"):
                    resource_streak[name] = resource_streak[name] + 1 if crv == name else 0
                if crv and resource_streak[crv] >= 2:
                    self.verdict, self.breach = crv, confirm.get("resource_breach")
                    return
                confirmed_growing = (growing
                    and confirm["backlog_slope_lb"] is not None
                    and confirm["backlog_slope_lb"] > 0)
                cworst = confirm.get("worst_error_type")
                confirmed_failing = (failing and (
                    confirm["err_rate"] > float(self.cfg["error_rate_limit"])
                    or bool(cworst and confirm["per_type"][cworst]["decided"]
                            and confirm["per_type"][cworst]["err_rate"]
                            > float(self.cfg["slo_err"]))
                    or confirm["rejected"] > 0))
                if confirmed_growing or confirmed_failing:
                    reason = "backlog_growth" if confirmed_growing else "technical_failure"
                    self.failure_onset = {
                        "offered_rate": round(rate, 2),
                        "achieved_rate": confirm["achieved_rate"],
                        "reason": reason}
                    self.verdict = "queue_divergence" if confirmed_growing else "errors"
                    profile = ("aggregate" if confirmed_growing
                               else (cworst or "aggregate"))
                    value = (confirm["backlog_slope_lb"] if confirmed_growing
                             else (confirm["per_type"][cworst]["err_rate"]
                                   if cworst else confirm["err_rate"]))
                    self.breach = {
                        "profile": profile,
                        "metric": "backlog_growth" if confirmed_growing else "error_rate",
                        "value": value,
                        "limit": 0.0 if confirmed_growing else (
                            float(self.cfg["slo_err"]) if cworst
                            else float(self.cfg["error_rate_limit"]))}
                    return
            if rate >= float(self.cfg["arrival_max_rate"]):
                self.verdict = "capped"
                return
            if (self.cfg.get("max_duration_s") is not None
                    and time.time() - self.started_at > float(self.cfg["max_duration_s"])):
                self.verdict = "timeout"
                return
            step_factor = float(self.cfg["arrival_step_factor"])
            utilization = (level["clean_rate"] / level["achieved_rate"]
                           if level["achieved_rate"] > 0 else 0.0)
            # The geometric ramp is deliberately coarse while the host keeps
            # up.  Once clean output begins to trail admitted work—or a
            # suspected boundary fails confirmation—insert denser design
            # points around the knee before the eventual breakpoint fit.
            if (utilization < float(self.cfg["arrival_refine_utilization"])
                    or growing or failing):
                step_factor = max(1.01, step_factor ** 0.5)
            level["next_step_factor"] = round(step_factor, 4)
            rate = min(float(self.cfg["arrival_max_rate"]), rate * step_factor)

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
        rates = [lv.get("achieved_rate", lv["offered_rate"])
                 for lv in self.rate_levels]
        clean = [lv["clean_rate"] for lv in self.rate_levels]
        fit = st.bootstrap_breakpoint_ci(rates, clean, seed=self.seed or 0)
        if fit is None:
            self.capacity_detail = {"status": "no distinct capacity knee detected",
                                    "levels": self.rate_levels}
            return
        estimate = float(fit["estimate"])
        low = float(fit["lower_bound_95"])
        ci95 = list(fit["ci95"])
        self.capacity_wps = round(low, 2)
        self.capacity_detail = {
            "status": "measured",
            "clean_workflows_per_s": round(low, 2),
            "breakpoint_estimate": round(estimate, 2),
            "lower_bound_95": round(low, 2),
            "ci95": [round(float(ci95[0]), 2), round(float(ci95[1]), 2)],
            "fit_rate_basis": "achieved admission rate",
            "confirmed_divergence_rate": (self.failure_onset or {}).get("achieved_rate"),
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
                    k: round(statistics.mean(tr.get(k, 0) for tr in traces), 1)
                    for k in ("llm_calls", "steps", "validations", "task_count",
                              "tool_calls")
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
        mock_tier = None
        if self.inference_backend == "remote_mock":
            try:
                from backend.capacity import mockrouter
                mock_tier = mockrouter.metadata(self._router_base_url)
            except Exception:  # noqa: BLE001
                mock_tier = None
        publication_exclusion = None
        if (self.benchmark_target == "agent_host"
                and self.inference_backend == "remote_mock"):
            if not mock_tier or not mock_tier.get("isolated_from_host"):
                publication_exclusion = (
                    "inference stand-in was co-located with the agent host")
            else:
                observed_wps = max(
                    [float(lv.get("achieved_rate") or 0) for lv in self.rate_levels]
                    + [float(hold.get("rpm") or 0) / 60.0])
                expected_calls = statistics.mean([
                    ((wf.get("contract") or {}).get("llm_calls") or [1, 1])[1]
                    for wf in self.scenarios.values()])
                required_rps = round(2.0 * observed_wps * expected_calls, 2)
                mock_tier["required_headroom_requests_per_s"] = required_rps
                certified_rps = mock_tier.get("certified_requests_per_s")
                mock_tier["headroom_qualified"] = bool(
                    certified_rps is not None and certified_rps >= required_rps)
                if not mock_tier["headroom_qualified"]:
                    publication_exclusion = (
                        "inference stand-in lacks independent 2× request-rate "
                        "headroom qualification")
        publication_eligible = publication_exclusion is None
        self.result = {
            "mode": self.mode,
            "benchmark_target": self.benchmark_target,
            "inference_backend": self.inference_backend,
            "verdict": verdict,
            "result_kind": kind,
            "censored": kind == "lower_bound",
            "censor_reason": (CENSOR_REASON.get(verdict or "", verdict)
                              if kind == "lower_bound" else None),
            "publication_eligible": publication_eligible,
            "publication_exclusion": publication_exclusion,
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
            "arrival_calibration": self.arrival_calibration,
            "failure_onset": self.failure_onset,
            "load_model": str(self.cfg["load_model"]),
            "service_class": str(self.cfg["service_class"]),
            "service_rung": self.assigned_rung,
            "service_ladder": self.ladder or None,
            "weigh_in": self.weigh_in or None,
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
            "cancelled_requests": self.cancelled_requests,
            "unfinished_requests": max(
                0, self.total_requests - completed_requests - self.cancelled_requests),
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
                        "mock_ms", "mock_sigma", "arrival_start_rate",
                        "arrival_step_factor", "arrival_max_rate", "arrival_hold_s",
                        "arrival_refine_utilization",
                        "capability_target", "capability_confidence",
                        "capability_min_samples", "harness_tolerance")},
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
                "mock_tier": mock_tier,
                "prompt_corpus": ("seeded trace-id suffix v2"
                                   if os.getenv("CAPACITY_PROMPT_SUFFIX", "1") != "0"
                                   else "suffix disabled (calibration)"),
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
