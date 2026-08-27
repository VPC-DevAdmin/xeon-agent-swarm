"""The two reported metrics: service capability and sustainable capacity.

Capability is a session count measured against a declared deadline with a
confidence bound. Capacity is a clean workflow rate measured against queue
divergence under open-loop arrivals. These tests hold the two apart and check
the statistics they rest on.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.capacity import controller as ctl
from backend.capacity import stats as st


# ── statistics ───────────────────────────────────────────────────────────────

def test_confidence_bound_refuses_to_certify_a_tiny_sample():
    """Two clean completions are 100 percent observed and prove nothing."""
    assert st.wilson_lower(2, 2) < 0.5
    assert st.wilson_lower(20, 20) < 0.95
    assert st.wilson_lower(60, 60) >= 0.95


def test_sample_cost_of_a_95_95_claim():
    n = st.samples_for_bound(0.95)
    assert 40 <= n <= 80                      # the real price of the claim
    assert st.wilson_lower(n, n) >= 0.95
    assert st.wilson_lower(n - 1, n) < 0.95   # one failure at the floor fails


def test_backlog_slope_bound_separates_scatter_from_growth():
    xs = list(range(24))
    flat = [40 + (i % 5) - 2 for i in xs]         # noisy but level
    rising = [40 + 3 * i + (i % 5) for i in xs]   # unmistakably growing
    assert st.slope_lower_bound(xs, flat) <= 0
    assert st.slope_lower_bound(xs, rising) > 0


def test_breakpoint_is_found_when_present_and_refused_when_absent():
    rates = [10, 20, 30, 40, 50, 60, 70, 80]
    saturating = [10, 20, 30, 40, 48, 50, 50, 50]
    fit = st.bootstrap_breakpoint_ci(rates, saturating, seed=7)
    assert fit is not None
    estimate, low, high = fit
    assert low <= estimate <= high
    assert 30 <= estimate <= 70
    # a host that never saturated must not be handed a boundary
    assert st.segmented_breakpoint(rates, list(rates)) is None


# ── capability ───────────────────────────────────────────────────────────────

def _cfg(**over):
    cfg = dict(mock_ms=25, mock_sigma=2, step_interval_s=0.4, hold_s=1.0,
               sample_interval_s=0.1, max_users=3, start_users=1, step_users=1,
               max_duration_s=20, plateau_frac=0, warmup_s=0, seed=42,
               min_samples=1)
    cfg.update(over)
    return cfg


def _seed_cohort(test, sid, n, *, late=0, since=None, latency_ms=1000.0):
    now = ctl.time.time()
    since = since if since is not None else now - 30
    for i in range(n):
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0,
                           "ts": since + 1 + i * 0.01, "t_submit": since + 0.5 + i * 0.01})
    for i in range(late):
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": 99_000.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": since + 2 + i * 0.01, "t_submit": since + 0.5 + i * 0.01})
    return since


def test_capability_needs_the_declared_deadline():
    """Without a declared deadline the metric is not configured. It is never
    inferred from the watchdog."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    for wf in test.scenarios.values():
        wf["deadlines"] = None
    assert test.deadlines_configured() is False
    state, _ = test._capability_state(ctl.time.time() - 60)
    assert state == "unconfigured"


def test_capability_passes_only_with_enough_evidence():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    since = ctl.time.time() - 30
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 10, since=since)     # clean but far too few
    assert test._capability_state(since)[0] == "inconclusive"
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 60, since=since)     # now past the sample floor
    assert test._capability_state(since)[0] == "good"


def test_late_workflows_fail_capability_even_when_they_succeed():
    """On-deadline is part of success: a correct answer after the deadline is
    a capability failure, and the breach names the type."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    since = ctl.time.time() - 30
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 60, since=since)
    slow = test.scenario_ids[0]
    _seed_cohort(test, slow, 0, late=12, since=since)
    state, breach = test._capability_state(since)
    assert state == "bad"
    assert breach["profile"] == slow and breach["metric"] == "capability"
    assert breach["value"] < 0.95 and breach["deadline_s"] == 30


def test_running_work_inside_its_deadline_counts_neither_way():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    sid = test.scenario_ids[0]
    since = ctl.time.time() - 5
    test._inflight[1] = (sid, since + 1)          # young, still allowed to finish
    successes, decided, pending = test._capability_cohort(sid, since)
    assert (successes, decided, pending) == (0, 0, 1)


def test_work_past_its_deadline_is_a_failure_before_it_finishes():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    sid = test.scenario_ids[0]
    since = ctl.time.time() - 120
    test._inflight[1] = (sid, since + 1)          # older than the 30s deadline
    successes, decided, pending = test._capability_cohort(sid, since)
    assert (successes, decided, pending) == (0, 1, 0)


# ── capacity ─────────────────────────────────────────────────────────────────

def test_open_loop_submits_without_waiting_for_completions():
    """The generator's schedule is independent of completion, which is what
    lets a backlog form at all."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    submitted = []

    async def slow_submit(sid, idx):
        submitted.append(sid)
        test._admit(sid)                          # never released: work piles up
        await asyncio.sleep(5)

    test._submit_open = slow_submit

    async def go():
        test.offered_rate = 40.0
        task = asyncio.create_task(test._arrival_loop())
        await asyncio.sleep(0.5)
        test._stop.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    assert len(submitted) > 5                     # kept submitting while work ran
    assert len(test._inflight) == len(submitted)  # and the backlog grew


def test_open_loop_bounds_its_queue_instead_of_growing_without_limit():
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open", max_backlog=5),
                            mix="tile")

    async def never_finishes(sid, idx):
        test._admit(sid)
        await asyncio.sleep(30)

    test._submit_open = never_finishes

    async def go():
        test.offered_rate = 200.0
        task = asyncio.create_task(test._arrival_loop())
        await asyncio.sleep(0.4)
        test._stop.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    assert len(test._inflight) <= 5
    assert test.rejected > 0                      # counted, not silently dropped


def test_capacity_summary_reports_the_conservative_bound():
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.rate_levels = [
        {"offered_rate": r, "clean_rate": min(r, 46.0)}
        for r in (10, 20, 30, 40, 50, 60, 70)
    ]
    test._summarize_capacity()
    detail = test.capacity_detail
    assert detail["status"] == "measured"
    assert detail["clean_workflows_per_s"] == test.capacity_wps
    low, high = detail["ci95"]
    assert low <= detail["breakpoint_estimate"] <= high
    assert test.capacity_wps == low            # published number is the lower bound


def test_capacity_refuses_to_invent_a_knee():
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.rate_levels = [{"offered_rate": r, "clean_rate": r}
                        for r in (10, 20, 30, 40, 50, 60)]
    test._summarize_capacity()
    assert test.capacity_detail["status"] == "no distinct capacity knee detected"
    assert test.capacity_wps is None


def test_the_two_metrics_are_reported_separately(tmp_path, monkeypatch):
    """Capability and capacity carry different units and never merge into one
    unlabelled number."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _cfg())
    for scen in test.scenarios.values():
        scen["think_ms"] = 30
    asyncio.run(test.run())
    r = test.result
    assert "capability" in r and "sustainable_capacity" in r
    assert "capacity_workflows_per_s" in r
    assert r["load_model"] == "closed"
    assert r["service_class"] == "interactive"
    # the closed-loop diagnostic keeps its own name and is not the headline
    assert "stability_ceiling_users" in r


# ── what a result MEANS ──────────────────────────────────────────────────────

def _stopped_at(verdict, **fields):
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.verdict = verdict
    for k, v in fields.items():
        setattr(test, k, v)
    return test


def test_a_resource_stop_is_a_lower_bound_not_a_capacity():
    """Running out of CPU is a fact about the box's headroom, not a service
    boundary. The levels above were never tested, so the number is a floor."""
    for verdict in ("cpu", "memory", "kv", "spend_guard", "capped", "timeout",
                    "interference", "stopped"):
        test = _stopped_at(verdict, capacity_users=400)
        assert test._result_kind(verdict) == "lower_bound", verdict


def test_a_service_boundary_is_a_measurement():
    for verdict in ("unstable", "errors", "queue_divergence"):
        test = _stopped_at(verdict, capacity_users=400)
        assert test._result_kind(verdict) == "boundary", verdict


def test_a_run_that_produced_no_number_is_inconclusive_however_it_ended():
    for verdict in ("cpu", "unstable", "stopped", None):
        test = _stopped_at(verdict)
        assert test._result_kind(verdict) == "inconclusive", verdict


def test_an_invalid_run_is_not_even_a_lower_bound():
    """A run whose workload or harness failed its own integrity check measured
    nothing, so its number does not bound anything."""
    for verdict in ("workload_invalid", "harness_degraded"):
        test = _stopped_at(verdict, capacity_users=400)
        assert test._result_kind(verdict) == "invalid", verdict


def test_the_published_result_says_which_kind_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = _stopped_at("cpu", capacity_users=400, capacity_tiles=4)
    test.ended_at = ctl.time.time()
    test._finalize()
    r = test.result
    assert r["result_kind"] == "lower_bound"
    assert r["censored"] is True
    assert "CPU" in r["censor_reason"]
    # the number survives — it is real, it is just a floor
    assert r["capacity_users"] == 400
    assert r["capacity_certified"] is False


def test_a_certified_result_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = _stopped_at("unstable", capacity_users=400)
    test.ended_at = ctl.time.time()
    test._finalize()
    assert test.result["capacity_certified"] is True
    assert test.result["censored"] is False
    assert test.result["censor_reason"] is None


def test_open_loop_refuses_a_knee_when_the_backlog_never_diverged():
    """A straight line has no knee. A segmented fit will happily place one in
    the noise at the top, which would invent a boundary the host never showed."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.rate_levels = [{"offered_rate": r, "clean_rate": r}
                        for r in (10, 20, 30, 40, 50, 60)]
    test.verdict = "capped"          # hit the configured ceiling, still healthy
    test.failure_onset = None
    test._summarize_capacity()
    detail = test.capacity_detail
    assert detail["status"] == "lower bound"
    assert detail["at_least_workflows_per_s"] == 60
    assert test.capacity_wps == 60
    assert "ci95" not in detail and "breakpoint_estimate" not in detail


def test_open_loop_still_fits_when_divergence_was_confirmed():
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.rate_levels = [{"offered_rate": r, "clean_rate": min(r, 46.0)}
                        for r in (10, 20, 30, 40, 50, 60, 70)]
    test.verdict = "queue_divergence"
    test.failure_onset = {"offered_rate": 60, "reason": "backlog_growth"}
    test._summarize_capacity()
    assert test.capacity_detail["status"] == "measured"


def test_capability_passing_at_the_first_level_tested_is_a_floor():
    """Nothing above it was ever put to the deadline, so it bounds rather than
    measures. Only a pass BELOW a failure has bracketed the boundary."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    test.users = [None] * (test.tile_size or 1)
    test._capability_state = lambda since: ("good", None)
    asyncio.run(test._certify_capability())
    assert test.capability_detail["status"] == "lower bound"
    assert "no higher level" in test.capability_detail["reason"]
    assert test.capability_users == len(test.users)


def test_a_hand_stopped_open_loop_run_does_not_get_a_fitted_knee():
    """Stopping by hand leaves no verdict, only a phase. It censors the run
    exactly as a configured ceiling does."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.rate_levels = [{"offered_rate": r, "clean_rate": min(r, 46.0)}
                        for r in (10, 20, 30, 40, 50, 60, 70)]
    test.stop()
    assert test.verdict is None and test.phase == "stopped"
    test._summarize_capacity()
    assert test.capacity_detail["status"] == "lower bound"
    assert test._result_kind("stopped") == "lower_bound"
