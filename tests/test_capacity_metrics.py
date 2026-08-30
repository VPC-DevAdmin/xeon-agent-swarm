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
    z = st.familywise_z(3, 0.95)
    n = st.samples_for_bound(0.95, z)
    assert 80 <= n <= 100                     # joint claim across three types
    assert st.wilson_lower(n, n, z) >= 0.95
    assert st.wilson_lower(n - 1, n, z) < 0.95


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
    estimate = fit["estimate"]
    low, high = fit["ci95"]
    assert low <= estimate <= high
    assert fit["lower_bound_95"] <= estimate
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


def test_capability_needs_the_declared_ladder():
    """Without a declared ladder the metric is not configured, and without an
    assigned rung no deadline exists. Neither is ever inferred from the
    watchdog or the host."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.ladder = {}
    assert test.deadlines_configured() is False
    test.ladder = {"conversational": 30.0}
    assert test._deadline_s("any") is None            # no rung assigned yet
    state, _ = test._capability_state(ctl.time.time() - 60)
    assert state == "unconfigured"


def test_capability_passes_only_with_enough_evidence():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.assigned_rung = "conversational"
    test.user_scenario = list(test.scenario_ids)
    since = ctl.time.time() - 30
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 10, since=since)     # clean but far too few
    assert test._capability_state(since)[0] == "inconclusive"
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 100, since=since)    # now past joint-confidence floor
    assert test._capability_state(since)[0] == "good"


def test_late_workflows_fail_capability_even_when_they_succeed():
    """On-deadline is part of success: a correct answer after the deadline is
    a capability failure, and the breach names the type."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.assigned_rung = "conversational"
    test.user_scenario = list(test.scenario_ids)
    since = ctl.time.time() - 30
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 100, since=since)
    slow = test.scenario_ids[0]
    _seed_cohort(test, slow, 0, late=12, since=since)
    state, breach = test._capability_state(since)
    assert state == "bad"
    assert breach["profile"] == slow and breach["metric"] == "capability"
    assert breach["value"] < 0.95 and breach["deadline_s"] == 15


def test_running_work_inside_its_deadline_counts_neither_way():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.assigned_rung = "conversational"
    sid = test.scenario_ids[0]
    since = ctl.time.time() - 5
    test._inflight[1] = (sid, since + 1)          # young, still allowed to finish
    successes, decided, pending = test._capability_cohort(sid, since)
    assert (successes, decided, pending) == (0, 0, 1)


def test_work_past_its_deadline_is_a_failure_before_it_finishes():
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.assigned_rung = "conversational"
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
    assert test.capacity_wps == detail["lower_bound_95"]
    assert detail["fit_rate_basis"] == "achieved admission rate"


def test_queue_growth_uses_increments_not_ols_on_integrated_levels():
    # A high but stationary queue with serial waves must not become growth just
    # because adjacent levels share almost all their state.
    xs = [float(i) for i in range(40)]
    stationary = [100 + (i % 8) for i in range(40)]
    rising = [100 + i * 2 + (i % 4) for i in range(40)]
    assert st.queue_growth_lower_bound(xs, stationary, seed=9) <= 0
    assert st.queue_growth_lower_bound(xs, rising, seed=9) > 0


def test_open_loop_confirms_in_a_second_window_at_the_same_rate():
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", arrival_start_rate=20, arrival_max_rate=100), mix="tile")
    calls = []

    async def idle_generator():
        await test._stop.wait()

    async def measure(rate, *, confirm=False):
        calls.append((rate, confirm))
        return {"generator_ok": True, "backlog_slope_lb": 1.0,
                "err_rate": 0.0, "per_type": {}, "worst_error_type": None,
                "rejected": 0, "achieved_rate": rate, "clean_rate": rate - 1,
                "offered_rate": rate, "resource_verdict": None}

    test._arrival_loop = idle_generator
    test._measure_open_level = measure
    asyncio.run(test._rate_ramp())
    assert calls == [(20.0, False), (20.0, True)]
    assert test.verdict == "queue_divergence"


def test_open_loop_adds_denser_rate_points_near_the_knee():
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", arrival_start_rate=10, arrival_step_factor=4,
        arrival_max_rate=100), mix="tile")
    calls = []

    async def idle_generator():
        await test._stop.wait()

    async def measure(rate, *, confirm=False):
        calls.append((rate, confirm))
        growing = rate >= 20
        return {"generator_ok": True, "backlog_slope_lb": 1.0 if growing else None,
                "err_rate": 0.0, "per_type": {}, "worst_error_type": None,
                "rejected": 0, "achieved_rate": rate,
                # 90% completion utilization at the first otherwise-stable
                # level should replace the 4x jump with sqrt(4)=2x.
                "clean_rate": rate * 0.9, "offered_rate": rate,
                "resource_verdict": None}

    test._arrival_loop = idle_generator
    test._measure_open_level = measure
    asyncio.run(test._rate_ramp())
    assert calls == [(10.0, False), (20.0, False), (20.0, True)]
    assert test.rate_levels[0]["next_step_factor"] == 2.0


def test_open_loop_cloud_submission_honours_the_spend_guard():
    endpoint = {"input_per_mtok": 1.0, "output_per_mtok": 3.0}
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", max_cost_usd=0.01), mix="tile", endpoint=endpoint)
    asyncio.run(test._submit_open(test.scenario_ids[0], 1))
    assert test.verdict == "spend_guard"
    assert test.total_requests == 0


def test_open_loop_resource_stop_censors_before_calling_queue_capacity():
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", arrival_start_rate=20, arrival_max_rate=100), mix="tile")
    observations = iter(["cpu", "cpu"])

    async def idle_generator():
        await test._stop.wait()

    async def measure(rate, *, confirm=False):
        rv = next(observations)
        return {"generator_ok": True, "backlog_slope_lb": None,
                "err_rate": 0.0, "per_type": {}, "worst_error_type": None,
                "rejected": 0, "achieved_rate": rate, "clean_rate": rate,
                "offered_rate": rate, "resource_verdict": rv,
                "resource_breach": {"profile": "host", "metric": "cpu_pct",
                                     "value": 91, "limit": 90}}

    test._arrival_loop = idle_generator
    test._measure_open_level = measure
    asyncio.run(test._rate_ramp())
    assert test.verdict == "cpu"
    test.capacity_wps = 19.0
    assert test._result_kind("cpu") == "lower_bound"


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
    test.assigned_rung = "conversational"
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


# ── the generator's own honesty ──────────────────────────────────────────────

def test_the_arrival_loop_delivers_its_schedule_at_high_rate():
    """A per-submission sleep of 1/rate silently falls behind past a few
    hundred per second. The batched loop must actually deliver — and count —
    the rate it claims."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")

    async def instant(sid, idx):
        pass

    test._submit_open = instant

    async def go():
        test.offered_rate = 500.0
        task = asyncio.create_task(test._arrival_loop())
        await asyncio.sleep(1.0)
        test._stop.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    # 500/s for ~1s: the due-counter self-corrects late ticks, so the count
    # must land close. The old loop delivered a fraction of this.
    assert 400 <= test._arrivals <= 600


def test_open_loop_tasks_are_pruned_as_they_finish():
    """One task object per unit, retained forever, is an unbounded leak at
    sustained rates."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")

    async def instant(sid, idx):
        pass

    test._submit_open = instant
    tasks_before = len(test._tasks)

    async def go():
        test.offered_rate = 300.0
        task = asyncio.create_task(test._arrival_loop())
        await asyncio.sleep(0.5)
        test._stop.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)          # let done-callbacks run

    asyncio.run(go())
    assert test._arrivals > 100                    # real volume went through
    assert len(test._open_tasks) <= 5              # and none of it is retained
    assert len(test._tasks) == tasks_before        # the run-scoped list is untouched


def test_a_generator_that_falls_behind_censors_the_run():
    """A level judged at an offered rate the generator never delivered would
    be a conclusion about load nobody offered."""
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", arrival_start_rate=100.0, arrival_hold_s=0.4),
        mix="tile")

    async def dead_generator():
        await test._stop.wait()          # fires nothing: achieved rate is 0

    test._arrival_loop = dead_generator
    asyncio.run(test._rate_ramp())
    assert test.verdict == "generator_limit"
    assert test.breach["metric"] == "achieved_rate"
    # With nothing delivered there is no number to bound: inconclusive.
    assert test._result_kind("generator_limit") == "inconclusive"
    # Once lower levels HAVE produced a clean rate, the stop bounds it.
    test.capacity_wps = 12.0
    assert test._result_kind("generator_limit") == "lower_bound"
    assert "harness limit" in ctl.CENSOR_REASON["generator_limit"]


def test_each_level_records_achieved_rate_beside_offered():
    """The offered rate is a claim; the achieved rate is the receipt."""
    test = ctl.CapacityTest("e2e", [], _cfg(
        load_model="open", arrival_start_rate=50.0, arrival_hold_s=0.6,
        max_duration_s=1.2), mix="tile")

    async def instant(sid, idx):
        key = test._admit(sid)
        test._release(key)

    test._submit_open = instant
    asyncio.run(test._rate_ramp())
    assert test.rate_levels, "no level was recorded"
    lv = test.rate_levels[0]
    assert lv["offered_rate"] == 50.0
    assert "achieved_rate" in lv and "control_cpu_pct" in lv
    # measured over the second half-hold, self-corrected ticks: close to offered
    assert 40.0 <= lv["achieved_rate"] <= 60.0


# ── the service ladder and the weigh-in ──────────────────────────────────────

def _weighed(medians_ms: dict[str, float], **over):
    """A test poised at the weigh-in with seeded completions per type."""
    test = ctl.CapacityTest("e2e", [], _cfg(**over), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    now = ctl.time.time()
    for sid, ms in medians_ms.items():
        for i in range(test.weigh_in_cfg["samples_per_type"]):
            test.calls.append({"scenario": sid, "ok": True, "durable": True,
                               "latency_ms": ms, "tokens_in": 0, "tokens_out": 0,
                               "ts": now - 5 + i * 0.01, "t_submit": now - 6})
    return test


def test_weigh_in_places_the_machine_in_its_tier():
    """A 12s median lands in `interactive` (ceiling 15s), whose 45s deadline
    gives it 3x its own speed as headroom."""
    test = _weighed({sid: 12_000.0 for sid in
                     ctl.CapacityTest("e2e", [], _cfg(), mix="tile").scenario_ids})
    assert asyncio.run(test._weigh_in()) is True
    assert test.assigned_rung == "interactive"
    assert test._deadline_s("any") == 45.0
    assert test.weigh_in["override"] is False


def test_weigh_in_places_on_the_worst_type():
    """One fast workflow must not carry a slow one into a deadline it cannot
    meet: two types at 12s and one at 90s place the machine by the 90s."""
    ids = ctl.CapacityTest("e2e", [], _cfg(), mix="tile").scenario_ids
    medians = {sid: 12_000.0 for sid in ids}
    medians[ids[0]] = 90_000.0
    test = _weighed(medians)
    assert asyncio.run(test._weigh_in()) is True
    assert test.assigned_rung == "attended"      # 90s > 50s ceiling, <= 150s
    assert test.weigh_in["worst_median_s"] == 90.0


def test_the_slowest_machines_land_in_the_open_ended_tier():
    """NO MACHINE IS EXCLUDED. The last tier carries no ceiling, so even a
    very slow host is placed and measured rather than rejected."""
    ids = ctl.CapacityTest("e2e", [], _cfg(), mix="tile").scenario_ids
    test = _weighed({sid: 900_000.0 for sid in ids})   # 900s: past every ceiling
    assert asyncio.run(test._weigh_in()) is True
    assert test.assigned_rung == "background"
    assert test._deadline_s("any") == 3600.0


def test_every_tier_gives_at_least_3x_its_ceiling_as_deadline():
    """The tier ladder's shape: a machine at the top of its tier may degrade
    3x under load before missing its deadline."""
    from backend.capacity.scenarios import service_tiers
    tiers = service_tiers()
    assert 5 <= len(tiers) <= 6
    assert tiers[-1].get("max_median_s") is None      # catch-all, no exclusion
    for t in tiers:
        if t.get("max_median_s"):
            assert t["deadline_s"] >= 3 * t["max_median_s"]


def test_an_operator_override_is_used_but_never_hidden():
    test = ctl.CapacityTest("e2e", [], _cfg(service_rung="queued"), mix="tile")
    assert asyncio.run(test._weigh_in()) is True
    assert test.assigned_rung == "queued"
    assert test.weigh_in["override"] is True


def test_an_unknown_override_tier_is_an_error_not_a_guess():
    test = ctl.CapacityTest("e2e", [], _cfg(service_rung="warp_speed"), mix="tile")
    assert asyncio.run(test._weigh_in()) is False
    assert test.phase == "error" and "unknown service tier" in test.error


def test_rung_overlays_report_every_rung_from_the_same_cohort():
    """The certified claim belongs to the assigned rung; every other rung is
    an observed overlay so a reader with a different responsiveness need can
    still use the run."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.assigned_rung = "queued"
    test.user_scenario = list(test.scenario_ids)
    since = ctl.time.time() - 30
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 20, since=since, latency_ms=60_000.0)  # 60s units
    overlays = test._rung_overlays(since)
    assert set(overlays) == set(test.ladder)          # every tier reported
    assert overlays["queued"]["certified"] is True
    sid = test.scenario_ids[0]
    # 60s units beat responsive (150s) and up, miss interactive (45s) and down
    assert overlays["interactive"]["per_type"][sid]["observed"] == 0.0
    assert overlays["responsive"]["per_type"][sid]["observed"] == 1.0
    assert overlays["queued"]["per_type"][sid]["observed"] == 1.0


def test_starvation_condemns_only_idle_types_not_slow_ones():
    """One session per type with minutes-long units can empty a window while
    the unit is mid-flight. In flight means slow, not absent: the level
    dwells, and only a type with NOTHING running can starve a rung."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    slow = test.scenario_ids[0]
    # the slow type has a unit in flight and no completions in the window
    test._inflight[1] = (slow, ctl.time.time() - 30)
    for sid in test.scenario_ids[1:]:
        _seed_cohort(test, sid, 6)
    inflight_types = {p for p, _t in test._inflight.values()}
    assert slow in inflight_types            # the guard the ramp loop applies


def test_drift_condemns_a_matured_climbing_cohort():
    """Deterministic drift coverage: both halves matured, second half 40%
    hotter — the level is bad, mechanism named."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    now = ctl.time.time()
    test._rung_t0 = now - 20
    sid = test.scenario_ids[0]
    for i in range(8):     # older half: submitted 18-12s ago, flat 1000ms
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": 1000.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": now - 12 + i * 0.1, "t_submit": now - 18 + i * 0.5})
    for i in range(8):     # young half: submitted 9-3s ago, 1400ms and done
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": 1400.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": now - 3 + i * 0.1, "t_submit": now - 9 + i * 0.5})
    state, breach = test._evaluate_rung(20.0)
    assert state == "bad"
    assert breach["metric"] == "latency_unstable"


def test_an_immature_young_half_cannot_certify_a_level():
    """The young half's completions are its fast survivors: with most of its
    admissions still in flight, a flat-looking p80 is survivor bias and the
    level must read inconclusive, never good."""
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    test.user_scenario = list(test.scenario_ids)
    now = ctl.time.time()
    test._rung_t0 = now - 20
    sid = test.scenario_ids[0]
    for i in range(8):     # older half: matured, flat
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": 1000.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": now - 12 + i * 0.1, "t_submit": now - 18 + i * 0.5})
    for i in range(3):     # young half: three fast survivors completed...
        test.calls.append({"scenario": sid, "ok": True, "durable": True,
                           "latency_ms": 1000.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": now - 2 + i * 0.1, "t_submit": now - 8 + i * 0.5})
    for k in range(9):     # ...while nine admissions are still in flight
        test._inflight[100 + k] = (sid, now - 7 + k * 0.5)
    state, breach = test._evaluate_rung(20.0)
    assert state == "inconclusive"
    assert breach is None


# ── the machine profile ──────────────────────────────────────────────────────

def test_a_fresh_machine_profile_is_reused_instead_of_remeasured(
        tmp_path, monkeypatch):
    """A weigh-in characterizes the MACHINE, not the run. Re-measuring before
    every set costs ~20 minutes and re-rolls the draw's noise."""
    from backend.capacity import machine_profile as mp
    monkeypatch.setattr(mp, "PROFILE_PATH", tmp_path / "profiles.json")
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    fp = test._machine_fingerprint()
    mp.record(fp, {sid: 220.0 for sid in test.scenario_ids},
              commit="abc123", tiers=test.tiers)
    assert asyncio.run(test._weigh_in()) is True
    assert test.assigned_rung == "queued"
    assert test.weigh_in["source"] == "machine_profile"
    assert test.weigh_in["observation_count"] == 1


def test_pooled_observations_steady_a_borderline_machine(tmp_path, monkeypatch):
    """The anchor system landed on both sides of a boundary on different
    nights. Pooling every observation is a steadier statement than any single
    4-sample draw, and it only gets steadier."""
    from backend.capacity import machine_profile as mp
    monkeypatch.setattr(mp, "PROFILE_PATH", tmp_path / "profiles.json")
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    fp = test._machine_fingerprint()
    for worst in (380.0, 410.0, 395.0):        # straddling the 400s ceiling
        entry = mp.record(fp, {sid: worst for sid in test.scenario_ids},
                          commit="c", tiers=test.tiers)
    assert entry["observation_count"] == 3
    assert entry["pooled_worst_median_s"] == 395.0     # median, not last draw
    assert entry["tier"] == "queued"
    assert entry["observed_range_s"] == [380.0, 410.0]


def test_a_changed_machine_gets_a_new_fingerprint(tmp_path, monkeypatch):
    """A stale characterization must never follow a machine that is no longer
    the same machine."""
    from backend.capacity import machine_profile as mp
    base = dict(benchmark_target="integrated_node", inference_backend="local",
                benchmark_version=14, model="m",
                engine={"context_length": 32768}, host={"cpu_count": 64})
    fp = mp.fingerprint(**base)
    assert mp.fingerprint(**{**base, "benchmark_version": 15}) != fp
    assert mp.fingerprint(**{**base, "model": "other"}) != fp
    assert mp.fingerprint(**{**base, "engine": {"context_length": 8192}}) != fp
    assert mp.fingerprint(**{**base, "host": {"cpu_count": 32}}) != fp


def test_force_weigh_in_re_measures(tmp_path, monkeypatch):
    from backend.capacity import machine_profile as mp
    monkeypatch.setattr(mp, "PROFILE_PATH", tmp_path / "profiles.json")
    test = ctl.CapacityTest("e2e", [], _cfg(force_weigh_in=True), mix="tile")
    mp.record(test._machine_fingerprint(),
              {sid: 220.0 for sid in test.scenario_ids},
              commit="abc", tiers=test.tiers)
    test._stop.set()               # measure path would wait; stop immediately
    assert asyncio.run(test._weigh_in()) is False
    assert test.weigh_in.get("source") != "machine_profile"


# ── CPU attribution ──────────────────────────────────────────────────────────

def test_find_pids_matches_renamed_processes(tmp_path, monkeypatch):
    """A process that renames itself keeps a cmdline that no longer describes
    how it was launched. SGLang's scheduler does exactly this, and matching
    cmdline alone hid ~49% of the host in the residual bucket."""
    from backend.capacity import telemetry as tel
    root = tmp_path / "proc"
    for pid, cmdline, comm in (("100", "python -m sglang.launch_server", "python3"),
                               ("200", "sglang::scheduler", "sglang::schedul"),
                               ("300", "nginx -g daemon off", "nginx")):
        d = root / pid
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmdline.encode())
        (d / "comm").write_bytes(comm.encode())
    monkeypatch.setattr(tel.glob, "glob", lambda pat: [str(p) for p in root.iterdir()])
    found = set(tel.find_pids("sglang"))
    assert found == {100, 200}          # launcher by cmdline, scheduler by comm
    assert 300 not in found


def test_process_tree_pids_descends_to_unmatched_workers(tmp_path, monkeypatch):
    """Belt and braces: matching catches renamed workers, descent catches
    workers whose name matches nothing at all. The residual bucket decides
    whether a resource verdict means anything, so our own engine must never
    land in it — a saturating run would be reported as other tenants
    interfering with the benchmark."""
    from backend.capacity import telemetry as tel
    monkeypatch.setattr(tel, "find_pids", lambda pattern: [100])
    monkeypatch.setattr(tel, "descendant_pids",
                        lambda pid: [100, 200, 300] if pid == 100 else [pid])
    assert tel.process_tree_pids("sglang") == [100, 200, 300]


# ── arrival-schedule calibration ─────────────────────────────────────────────

def test_the_rate_search_is_aimed_at_the_measured_machine():
    """The shipped schedule opens at 2/s. A CPU-inference node services
    ~0.01/s, so an uncalibrated search starts ~180x above the drain rate:
    every level diverges and the fit gets no points below the knee."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.weigh_in = {"pooled_worst_median_s": 290.0}
    cal = test._calibrate_arrival_schedule()
    tile = test.tile_size or 1
    service = tile / 290.0
    assert cal["estimated_service_rate"] == round(service, 5)
    # opens below the service rate so the proportional segment gets points
    assert cal["start_rate"] < service
    # and caps far above it, so a machine that scales past one tile is not
    # capped before its own knee
    assert cal["max_rate"] > 10 * service
    assert "290s median" in cal["basis"]


def test_calibration_can_be_switched_off():
    """An operator who wants the declared schedule keeps it."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open",
                                            arrival_calibrated=False),
                            mix="tile")
    test.weigh_in = {"pooled_worst_median_s": 290.0}
    assert test._calibrate_arrival_schedule() is None


def test_calibration_needs_a_measurement_and_never_guesses():
    """With no median from any source there is nothing to aim at, and the
    declared schedule stands rather than being invented."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.weigh_in = {}
    assert test._calibrate_arrival_schedule() is None


def test_calibration_falls_back_to_observed_completions():
    """Without a weigh-in the run's own completions carry the estimate."""
    test = ctl.CapacityTest("e2e", [], _cfg(load_model="open"), mix="tile")
    test.weigh_in = {}
    for sid in test.scenario_ids:
        _seed_cohort(test, sid, 6, latency_ms=200_000.0)
    cal = test._calibrate_arrival_schedule()
    assert cal is not None
    assert cal["estimated_service_rate"] == round((test.tile_size or 1) / 200.0, 5)


def test_a_colocated_mock_is_recorded_not_refused(tmp_path, monkeypatch):
    """The reference-guide posture: a run is disqualified by what it hides,
    not by where its stand-in lived. Co-located mock runs stay publication
    eligible, with the stand-in's location, headroom math, and measured CPU
    share recorded for the reader."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile",
                            benchmark_target="agent_host",
                            inference_backend="remote_mock",
                            e2e_router={"base_url": "http://127.0.0.1:8901/v1"})
    test.ended_at = ctl.time.time()
    test._finalize()
    r = test.result
    assert r["publication_eligible"] is True
    assert r["publication_exclusion"] is None
    mt = (r.get("repro") or {}).get("mock_tier") or {}
    assert mt.get("loopback") is True            # the fact, stated
    assert "measured_cpu_pct" in mt              # and what it cost the host


# ── collapse-scoped callback accounting ──────────────────────────────────────

def test_callbacks_lost_in_collapse_do_not_invalidate(tmp_path, monkeypatch):
    """The 64-worker set lost 39 callbacks in one second of a condemned
    level's collapse at 191k clean requests and was refused whole. A loss in
    a collapse window belongs to wreckage that is never published; a loss
    during evidence-gathering still invalidates at zero tolerance."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    now = ctl.time.time()
    test.started_at = now - 100
    test.total_requests = 10_000
    test._collapse_windows = [[now - 30, now - 20]]
    times = [now - 25.0 + i * 0.01 for i in range(39)]     # inside collapse

    async def fake_counters():
        return {"persist_failures": 0, "callback_failures": 39,
                "callback_failure_times": times,
                "unreachable_executors": 0}

    import backend.workerpool as wp
    monkeypatch.setattr(wp, "collect_counters", fake_counters)
    test._harness_start = {"persist_failures": 0, "callback_failures": 0}
    asyncio.run(test._reconcile_harness())
    assert test.harness["callback_failures_collapse"] == 39
    assert test.harness["callback_failures"] == 0
    assert test.harness["ok"] is True
    assert test.verdict != "harness_degraded"


def test_callbacks_lost_during_evidence_still_invalidate(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _cfg(), mix="tile")
    now = ctl.time.time()
    test.started_at = now - 100
    test.total_requests = 10_000
    test._collapse_windows = [[now - 30, now - 20]]
    times = [now - 60.0]                                    # mid-evidence

    async def fake_counters():
        return {"persist_failures": 0, "callback_failures": 1,
                "callback_failure_times": times,
                "unreachable_executors": 0}

    import backend.workerpool as wp
    monkeypatch.setattr(wp, "collect_counters", fake_counters)
    test._harness_start = {"persist_failures": 0, "callback_failures": 0}
    asyncio.run(test._reconcile_harness())
    assert test.harness["callback_failures"] == 1
    assert test.verdict == "harness_degraded"
