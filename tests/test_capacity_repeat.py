"""Repeat sets: three runs, a median, and a range.

A single benchmark run is a single sample. These tests hold the driver to the
two rules that make a set of runs mean something: every run in the set must be
the same benchmark, and a contaminated run must not be quietly averaged in.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.capacity import controller as ctl
from backend.capacity import repeat as rpt


class _FakeTest:
    """Stands in for a CapacityTest: the driver only needs run/result/phase."""

    def __init__(self, result: dict, phase: str = "done"):
        self.result, self.phase, self.error = result, phase, None

    async def run(self):
        await asyncio.sleep(0)

    def stop(self):
        self.phase = "stopped"

    def status(self):
        return {"active": False, "phase": self.phase}


def _result(*, capability=None, wps=None, ceiling=None, verdict="unstable",
            kind="boundary", censored=False, censor_reason=None,
            fingerprint="wl-abc", commit="c1", seed=1, history_id="h1"):
    return {
        "verdict": verdict, "result_kind": kind, "censored": censored,
        "censor_reason": censor_reason,
        "capability": ({"users": capability, "status": "measured"}
                       if capability is not None else None),
        "capacity_workflows_per_s": wps,
        "stability_ceiling_users": ceiling,
        "benchmark_target": "agent_host", "inference_backend": "remote_mock",
        "mix": "tile", "load_model": "closed", "service_class": "interactive",
        "repro": {"scenario_fingerprint": fingerprint, "git_commit": commit,
                  "benchmark_version": 7, "seed": seed},
        "history_id": history_id,
    }


def _run_set(results, tmp_path, monkeypatch, **over):
    """Drive a set over a scripted list of run outcomes."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    queue = list(results)

    def factory(seed, rung=None):
        res = queue.pop(0)
        return _FakeTest(res if isinstance(res, dict) else res[0],
                         phase="error" if not isinstance(res, dict) else "done")

    kwargs = dict(runs=3, seed=100, settle_s=0, quiet_timeout_s=0)
    kwargs.update(over)
    rs = rpt.RepeatSet(factory, **kwargs)
    asyncio.run(rs.run())
    return rs


# ── aggregation ──────────────────────────────────────────────────────────────

def test_a_set_reports_the_median_and_the_observed_range():
    agg = rpt.aggregate([_result(capability=100), _result(capability=140),
                         _result(capability=120)])
    cap = agg["service_capability"]
    assert cap["median"] == 120                 # not the mean of 120
    assert (cap["min"], cap["max"]) == (100, 140)
    assert cap["values"] == [100, 140, 120]
    assert cap["unit"] == "sessions"
    assert cap["spread_pct"] == pytest.approx(33.3, abs=0.1)


def test_the_median_resists_one_bad_run_where_a_mean_would_not():
    """With three samples one contaminated run drags a mean and cannot drag a
    median. That is the whole reason for the choice."""
    values = [_result(capability=100), _result(capability=104),
              _result(capability=400)]
    assert rpt.aggregate(values)["service_capability"]["median"] == 104


def test_each_metric_keeps_its_own_units():
    agg = rpt.aggregate([_result(capability=100, wps=8.5, ceiling=900),
                         _result(capability=110, wps=9.5, ceiling=950)])
    assert agg["service_capability"]["unit"] == "sessions"
    assert agg["sustainable_capacity"]["unit"] == "clean workflows/s"
    assert agg["stability_ceiling"]["unit"] == "sessions"
    assert agg["sustainable_capacity"]["median"] == 9.0


# ── comparability ────────────────────────────────────────────────────────────

def test_a_workload_change_mid_set_ends_it_rather_than_averaging_across(
        tmp_path, monkeypatch):
    """A redeploy between runs would otherwise produce a range that is really
    two different benchmarks blended together."""
    rs = _run_set([_result(capability=100),
                   _result(capability=180, fingerprint="wl-CHANGED"),
                   _result(capability=110)],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "incomplete"
    assert rs.result["runs_accepted"] == 1
    assert "not comparable" in rs.result["excluded"][0]["reason"]
    assert "scenario_fingerprint" in rs.result["excluded"][0]["reason"]


def test_a_commit_change_mid_set_is_caught_too(tmp_path, monkeypatch):
    rs = _run_set([_result(capability=100), _result(capability=105, commit="c2")],
                  tmp_path, monkeypatch, runs=2)
    assert rs.result["status"] == "incomplete"
    assert "git_commit" in rs.result["excluded"][0]["reason"]


# ── exclusion ────────────────────────────────────────────────────────────────

def test_a_contaminated_run_is_excluded_and_retried(tmp_path, monkeypatch):
    """Interference means the run measured the other tenants, not us."""
    rs = _run_set([_result(capability=100),
                   _result(verdict="interference", kind="lower_bound",
                           capability=40),
                   _result(capability=110),
                   _result(capability=105)],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert rs.result["runs_accepted"] == 3
    assert rs.result["runs_excluded"] == 1
    assert "other tenants" in rs.result["excluded"][0]["reason"]
    # the contaminated run's number is nowhere in the aggregate
    assert 40 not in rs.result["metrics"]["service_capability"]["values"]


def test_a_run_that_failed_its_own_integrity_check_is_not_a_sample(
        tmp_path, monkeypatch):
    rs = _run_set([_result(capability=100),
                   _result(verdict="harness_degraded", kind="invalid",
                           capability=90),
                   _result(capability=110),
                   _result(capability=105)],
                  tmp_path, monkeypatch)
    assert rs.result["runs_accepted"] == 3
    assert "integrity check" in rs.result["excluded"][0]["reason"]


def test_running_out_of_retries_reports_incomplete_not_a_median(
        tmp_path, monkeypatch):
    """Publishing a median over whatever survived would be the failure mode
    this whole driver exists to prevent."""
    rs = _run_set([_result(capability=100),
                   _result(verdict="interference", kind="lower_bound"),
                   _result(verdict="interference", kind="lower_bound"),
                   _result(verdict="interference", kind="lower_bound")],
                  tmp_path, monkeypatch, max_retries=2)
    assert rs.result["status"] == "incomplete"
    assert rs.result["runs_accepted"] == 1
    assert rs.result["runs_excluded"] == 3
    assert "retries" in rs.result["incomplete_reason"]
    assert rs.result["metrics"] is None


def test_a_run_with_no_usable_number_is_excluded(tmp_path, monkeypatch):
    rs = _run_set([_result(kind="inconclusive", verdict="stopped"),
                   _result(capability=100), _result(capability=110),
                   _result(capability=105)],
                  tmp_path, monkeypatch)
    assert rs.result["runs_accepted"] == 3
    assert "no usable number" in rs.result["excluded"][0]["reason"]


def test_missing_intended_metric_is_excluded_even_with_a_diagnostic_number(
        tmp_path, monkeypatch):
    rs = _run_set([_result(ceiling=900), _result(capability=100),
                   _result(capability=110), _result(capability=105)],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert "intended metric" in rs.result["excluded"][0]["reason"]
    assert rs.result["metrics"]["service_capability"]["n"] == 3


# ── censoring carries through ────────────────────────────────────────────────

def test_one_censored_run_makes_the_whole_set_a_lower_bound(
        tmp_path, monkeypatch):
    """The median of three floors is itself a floor."""
    rs = _run_set([_result(capability=100),
                   _result(capability=110, verdict="cpu", kind="lower_bound",
                           censored=True, censor_reason="host CPU saturated"),
                   _result(capability=105)],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert rs.result["censored"] is True
    assert rs.result["censor_reasons"] == ["host CPU saturated"]


def test_a_clean_set_is_not_flagged_as_a_bound(tmp_path, monkeypatch):
    rs = _run_set([_result(capability=100), _result(capability=110),
                   _result(capability=105)], tmp_path, monkeypatch)
    assert rs.result["censored"] is False
    assert rs.result["status"] == "complete"


# ── bookkeeping ──────────────────────────────────────────────────────────────

def test_the_set_records_its_children_so_it_survives_a_restart(
        tmp_path, monkeypatch):
    rs = _run_set([_result(capability=100, history_id="a"),
                   _result(capability=110, history_id="b"),
                   _result(capability=105, history_id="c")],
                  tmp_path, monkeypatch)
    assert rs.result["child_run_ids"] == ["a", "b", "c"]
    assert list(tmp_path.glob("repeat-*.json"))       # written as it went


def test_every_run_gets_its_own_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    seeds = []

    def factory(seed, rung=None):
        seeds.append(seed)
        return _FakeTest(_result(capability=100 + len(seeds)))

    rs = rpt.RepeatSet(factory, runs=3, seed=500, settle_s=0, quiet_timeout_s=0)
    asyncio.run(rs.run())
    assert seeds == [500, 501, 502]
    assert rs.result["status"] == "complete"


def test_stopping_a_set_stops_the_run_underneath_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    rs = rpt.RepeatSet(lambda seed, rung=None: _FakeTest(_result(capability=100)),
                       runs=3, seed=1, settle_s=0, quiet_timeout_s=0)
    child = _FakeTest(_result(capability=100))
    rs.current = child
    rs.stop()
    assert child.phase == "stopped"
    assert rs.status()["active"] is False


def test_accepted_children_are_labelled_with_their_set(tmp_path, monkeypatch):
    """Set membership has to be visible in benchmark history, or three runs
    look like three unrelated results."""
    from backend.db import base as db_base
    from backend.repositories import capacity_runs as caps_repo

    labelled: list[tuple[str, str]] = []

    async def fake_set_label(session, rid, label):
        labelled.append((rid, label))

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass

    monkeypatch.setattr(caps_repo, "set_label", fake_set_label)
    monkeypatch.setattr(db_base, "get_sessionmaker", lambda: _FakeSession)

    rs = _run_set([_result(capability=100, history_id="a"),
                   _result(capability=110, history_id="b"),
                   _result(capability=105, history_id="c")],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert [r for r, _ in labelled] == ["a", "b", "c"]
    assert labelled[0][1] == f"set {rs.seed} \u00b7 run 1/3"


def test_a_labelling_failure_never_costs_a_good_run(tmp_path, monkeypatch):
    """History bookkeeping is not part of the measurement."""
    from backend.db import base as db_base

    def boom():
        raise RuntimeError("no database here")

    monkeypatch.setattr(db_base, "get_sessionmaker", boom)
    rs = _run_set([_result(capability=100), _result(capability=110),
                   _result(capability=105)], tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert rs.result["runs_accepted"] == 3
    assert rs.result["metrics"]["service_capability"]["median"] == 105


# ── the set builds identical runs ────────────────────────────────────────────

def test_a_set_builds_runs_that_differ_only_by_seed():
    """Resolving the plan once is what makes the runs comparable. If each run
    re-resolved its own config, a change between them would slip in unseen."""
    from backend.routers import capacity as rt

    plan = {"mode": "remote_mock", "target": "inference_engine",
            "inference_backend": "remote_mock", "mix": "tile",
            "cfg": {"seed": 7, "step_interval_s": 5.0}, "scenario_ids": [],
            "extra_workflows": {}, "e2e_router": None, "endpoint": None}
    a, b = rt._build_test(plan, 11), rt._build_test(plan, 12)
    assert (a.seed, b.seed) == (11, 12)
    assert a.scenario_ids == b.scenario_ids
    assert a.mix == b.mix and a.mode == b.mode
    assert a.cfg["step_interval_s"] == b.cfg["step_interval_s"]
    # the plan itself is untouched, so run three sees what run one saw
    assert plan["cfg"]["seed"] == 7


def test_one_measurement_at_a_time(monkeypatch):
    """A single run and a set share the same mutex — two at once would have
    them measuring each other."""
    from fastapi import HTTPException
    from backend.routers import capacity as rt

    class _Busy:
        def status(self): return {"active": True}

    monkeypatch.setattr(rt, "_current", None)
    monkeypatch.setattr(rt, "_repeat", _Busy())
    with pytest.raises(HTTPException) as exc:
        rt._require_idle()
    assert "repeat set is already running" in exc.value.detail

    monkeypatch.setattr(rt, "_repeat", None)
    monkeypatch.setattr(rt, "_current", _Busy())
    with pytest.raises(HTTPException):
        rt._require_idle()


def test_a_cloud_set_honours_the_dollar_guard_the_user_typed(monkeypatch):
    """The circuit breaker is per run. Three runs would otherwise spend three
    times the ceiling someone declared."""
    import asyncio as _asyncio

    from backend.routers import capacity as rt

    plan = {"mode": "e2e", "target": "agent_host",
            "inference_backend": "remote_real", "mix": "tile",
            "cfg": {"max_cost_usd": 30.0, "seed": None}, "scenario_ids": [],
            "extra_workflows": {}, "e2e_router": None, "endpoint": None}

    async def fake_prepare(_body):
        return plan

    started: dict = {}
    monkeypatch.setattr(rt, "_prepare", fake_prepare)
    monkeypatch.setattr(rt, "_require_idle", lambda: None)
    monkeypatch.setattr(rt, "_build_test", lambda p, seed=None: _FakeTest(_result()))
    monkeypatch.setattr(rt.asyncio, "create_task", lambda coro: coro.close())

    body = rt.RepeatBody(benchmark_target="agent_host",
                         inference_backend="remote_real", runs=3,
                         confirm_real=True, max_cost_usd=30.0)
    started = _asyncio.run(rt.start_repeat(body, None))
    assert started["max_cost_usd_total"] == 30.0
    assert started["max_cost_usd_per_run"] == 10.0
    assert plan["cfg"]["max_cost_usd"] == 10.0


# ── definitive findings publish; predetermined negatives are named ───────────

def _definitive(status, failed=None, **kw):
    r = _result(**kw)
    r["capability"] = {"users": None, "status": status, "definitive": True,
                       "highest_failed_users": failed}
    return r


def test_three_agreeing_measured_negatives_publish_as_a_finding(
        tmp_path, monkeypatch):
    """A level that failed with mature evidence is a measurement. Three
    children agreeing publish the negative instead of an incomplete set."""
    rs = _run_set([_definitive("not met at tested levels", failed=6)
                   for _ in range(3)], tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    out = rs.result["capability_outcome"]
    assert out["finding"] == "not met at tested levels"
    assert out["agreement"] == "3/3"
    assert out["highest_failed_users"] == [6, 6, 6]


def test_evidence_limited_hosts_publish_the_constraint_not_a_negative(
        tmp_path, monkeypatch):
    """A host whose ceiling sits below the conclusive cohort did not fail —
    the outcome was predetermined by sample economics, and the set says so."""
    rs = _run_set([_definitive("evidence limited: host ceiling below the "
                               "conclusive cohort") for _ in range(3)],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert "evidence limited" in rs.result["capability_outcome"]["finding"]


def test_disagreeing_children_are_not_a_result(tmp_path, monkeypatch):
    rs = _run_set([_definitive("not met at tested levels"),
                   _definitive("evidence limited: host ceiling below the "
                               "conclusive cohort"),
                   _definitive("not met at tested levels")],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "incomplete"
    assert rs.result["capability_outcome"] is None


def test_an_indefinite_child_is_still_not_a_sample(tmp_path, monkeypatch):
    r = _result()
    r["capability"] = {"users": None, "status": "stopped before certification",
                       "definitive": False}
    rs = _run_set([r, _definitive("not met at tested levels"),
                   _definitive("not met at tested levels"),
                   _definitive("not met at tested levels")],
                  tmp_path, monkeypatch)
    assert rs.result["runs_excluded"] == 1
    assert "did not produce its intended metric" in rs.result["excluded"][0]["reason"]
    assert rs.result["status"] == "complete"


def test_a_definitive_finding_counts_even_without_a_stability_number(
        tmp_path, monkeypatch):
    """Overnight set, child 3: same definitive evidence-limited finding as
    its siblings, excluded because its ramp broke before certifying any
    stability level, so result_kind read inconclusive. Whether a finding
    counts must never be a coin flip on an unrelated diagnostic."""
    r = _result(kind="inconclusive", verdict="errors")
    r["capability"] = {"users": None, "definitive": True,
                       "status": "evidence limited: host ceiling below the "
                                 "conclusive cohort"}
    rs = _run_set([r,
                   _definitive("evidence limited: host ceiling below the "
                               "conclusive cohort"),
                   _definitive("evidence limited: host ceiling below the "
                               "conclusive cohort")],
                  tmp_path, monkeypatch)
    assert rs.result["status"] == "complete"
    assert rs.result["runs_excluded"] == 0
    assert rs.result["capability_outcome"]["agreement"] == "3/3"
