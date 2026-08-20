"""Offline tests for the capacity tester: scenarios, telemetry parsers, the
mock-mode client, and a full (fast) controller lifecycle."""
from __future__ import annotations

import asyncio

import pytest

from backend.capacity import controller as ctl
from backend.capacity.client import StepCaller
from backend.capacity.scenarios import build_prompt, load_scenarios, scenario_list
from backend.capacity.telemetry import cpu_pct_from, parse_meminfo, parse_proc_stat


# ── scenarios ─────────────────────────────────────────────────────────────────

def test_five_scenarios_with_expected_shapes():
    items = scenario_list()
    assert len(items) == 5
    by_id = {s["id"]: s for s in items}
    assert by_id["quick_answer"]["calls_per_loop"] == 1
    assert by_id["deep_agent"]["calls_per_loop"] == 5
    assert all(s["tokens_out_per_loop"] > 0 for s in items)


def test_prompt_padding_tracks_target():
    step = load_scenarios()["summarizer"]["steps"][0]
    msgs = build_prompt(step, "Document summarizer")
    # ~4 chars/token; allow slack for the fixed preamble
    assert abs(len(msgs[1]["content"]) - step["prompt_tokens"] * 4) < 300


# ── telemetry parsers ─────────────────────────────────────────────────────────

def test_cpu_parse_and_delta():
    a = parse_proc_stat("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 0\n")
    b = parse_proc_stat("cpu  200 0 200 750 150 0 0 0 0 0\n")
    assert a and b
    # busy delta = (400-200)=200, total delta = (1300-1000)=300 → 66.7%
    assert cpu_pct_from(a, b) == pytest.approx(66.7, abs=0.1)


def test_meminfo_parse():
    text = "MemTotal:       1048576 kB\nMemFree: 1 kB\nMemAvailable:  262144 kB\n"
    used_gb, used_pct = parse_meminfo(text)
    assert used_pct == 75.0
    assert used_gb == pytest.approx(0.8, abs=0.05)


def test_parsers_tolerate_garbage():
    assert parse_proc_stat("nonsense") is None
    assert parse_meminfo("nonsense") is None


# ── mock-mode client ──────────────────────────────────────────────────────────

def test_mock_mode_bell_curve_latency():
    caller = StepCaller("remote_mock", mock_ms=80, mock_sigma=10)
    scen = load_scenarios()["quick_answer"]
    recs = [asyncio.run(caller.call(scen, scen["steps"][0])) for _ in range(8)]
    assert all(r["ok"] for r in recs)
    lats = [r["latency_ms"] for r in recs]
    assert 40 < min(lats) and max(lats) < 400          # clustered near the set point
    assert recs[0]["tokens_out"] == int(scen["steps"][0]["max_tokens"] * 0.8)


def test_unknown_mode_is_an_error_record_not_a_raise():
    caller = StepCaller("bogus")
    scen = load_scenarios()["quick_answer"]
    rec = asyncio.run(caller.call(scen, scen["steps"][0]))
    assert rec["ok"] is False and "bogus" in rec["error"]


# ── controller lifecycle (fast, all-mock) ─────────────────────────────────────

def _fast_cfg(**over):
    # plateau_gain=-1 disables the (noise-sensitive) plateau detector so the
    # fast lifecycle test deterministically ends at the user cap.
    cfg = dict(mock_ms=25, mock_sigma=4, step_interval_s=0.4, hold_s=2.5,
               sample_interval_s=0.1, max_users=3, start_users=1, step_users=1,
               max_duration_s=30, plateau_gain=-1.0)
    cfg.update(over)
    return cfg


def test_full_ramp_reaches_cap_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg())
    asyncio.run(test.run())
    r = test.result
    assert r is not None
    assert r["verdict"] == "capped"
    assert r["max_users"] == 3
    assert r["total_requests"] > 0
    assert len(r["per_scenario"]) == 5
    assert r["steady"]["p50_ms"] is not None
    assert list(tmp_path.glob("capacity-*.json"))      # persisted


def test_stop_mid_ramp(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["quick_answer"], _fast_cfg(max_users=50))

    async def go():
        task = asyncio.create_task(test.run())
        await asyncio.sleep(1.0)
        test.stop()
        await task

    asyncio.run(go())
    assert test.phase == "stopped"
    assert test.result["verdict"] == "stopped"
    assert test.result["per_scenario"]["quick_answer"]["calls"] > 0


def test_status_shape_while_idleish(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["deep_agent"], _fast_cfg())
    s = test.status()
    assert s["phase"] == "starting" and s["users"] == 0
    assert "per_scenario" in s and "deep_agent" in s["per_scenario"]
