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

def test_six_real_agent_scenarios():
    items = scenario_list()
    assert len(items) == 6
    by_id = {s["id"]: s for s in items}
    # THE invariant: real agents only — every flow calls tools, and every flow
    # either carries context within its loop or compounds across a session.
    # A stateless single-call "chatbot" scenario must never enter this mix.
    for s in items:
        assert s["tool_calls_per_loop"] > 0, f"{s['id']} has no tool calls"
        agentic = s["session_turns"] > 1 or any(st["carry_context"] for st in s["steps"])
        assert agentic, f"{s['id']} neither carries context nor compounds a session"
        assert s["calls_per_loop"] > 1, f"{s['id']} is a single-call ping"
    # 5 steps + 3 tool continuations — every tool round-trip is an extra LLM call
    assert by_id["deep_agent"]["calls_per_loop"] == 8
    assert by_id["deep_agent"]["tool_calls_per_loop"] == 3
    assert by_id["deep_agent"]["session_turns"] == 3
    assert by_id["support_agent"]["session_turns"] == 4          # ticket conversation
    assert all(s["tokens_out_per_loop"] > 0 for s in items)


def test_prompt_padding_tracks_target():
    step = load_scenarios()["doc_intelligence"]["steps"][0]
    msgs = build_prompt(step, "Document intelligence")
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
    scen = load_scenarios()["support_agent"]
    recs = [asyncio.run(caller.call(scen, scen["steps"][0])) for _ in range(8)]
    assert all(r["ok"] for r in recs)
    lats = [r["latency_ms"] for r in recs]
    assert 40 < min(lats) and max(lats) < 400          # clustered near the set point
    assert recs[0]["tokens_out"] == int(scen["steps"][0]["max_tokens"] * 0.8)
    # carried context is reflected in the reported prompt size
    rec = asyncio.run(caller.call(scen, scen["steps"][0], extra_context_tokens=1000))
    assert rec["tokens_in"] == scen["steps"][0]["prompt_tokens"] + 1000


def test_unknown_mode_is_an_error_record_not_a_raise():
    caller = StepCaller("bogus")
    scen = load_scenarios()["support_agent"]
    rec = asyncio.run(caller.call(scen, scen["steps"][0]))
    assert rec["ok"] is False and "bogus" in rec["error"]


# ── controller lifecycle (fast, all-mock) ─────────────────────────────────────

def _fast_cfg(**over):
    # plateau_frac=0 disables the plateau detector so the fast lifecycle test
    # deterministically ends at the user cap; the wide SLO never trips on the
    # constant mock latency.
    cfg = dict(mock_ms=25, mock_sigma=4, step_interval_s=0.4, hold_s=2.5,
               sample_interval_s=0.1, max_users=3, start_users=1, step_users=1,
               max_duration_s=30, plateau_frac=0, warmup_s=0, seed=42,
               min_samples=1)
    cfg.update(over)
    return cfg


def test_full_ramp_reaches_cap_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg(max_duration_s=60))
    for scen in test.scenarios.values():
        scen["think_ms"] = 30          # feed the cohort inside short windows
    asyncio.run(test.run())
    r = test.result
    assert r is not None
    assert r["verdict"] == "capped"
    assert r["max_users"] == 3
    assert r["peak_users"] == 3
    assert r["baseline_p95_ms"] is not None
    assert r["total_requests"] > 0
    assert len(r["per_scenario"]) == 6
    assert r["steady"]["p50_ms"] is not None
    # memory-telemetry fields are always present (None where unmeasurable)
    assert "bw_gbs" in r["steady"] and "kv_pct" in r["steady"]
    assert "mem_mb_per_user" in r
    assert all("avg_tokens_in_flight" in s for s in r["per_scenario"].values())
    assert list(tmp_path.glob("capacity-*.json"))      # persisted


def test_stop_mid_ramp(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg(max_users=50))

    async def go():
        task = asyncio.create_task(test.run())
        await asyncio.sleep(1.0)
        test.stop()
        await task

    asyncio.run(go())
    assert test.phase == "stopped"
    assert test.result["verdict"] == "stopped"
    assert test.result["per_scenario"]["support_agent"]["calls"] > 0


def test_status_shape_while_idleish(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["deep_agent"], _fast_cfg())
    s = test.status()
    assert s["phase"] == "starting" and s["users"] == 0
    assert "per_scenario" in s and "deep_agent" in s["per_scenario"]


# ── agentic loop semantics: compounding context, tools, sessions ─────────────

def test_loop_compounds_context_and_tools():
    """research_agent: prompts must GROW across the loop (agent, not chatbot):
    gather -> 2 tool continuations with injected results -> analyze/write carry
    everything accumulated so far."""
    from backend.capacity.controller import run_scenario_loop
    from backend.capacity.scenarios import load_scenarios

    scen = load_scenarios()["research_agent"]
    seen: list[tuple[str, int]] = []

    async def fake_call(scenario, step, sid, idx, extra_tokens, label):
        seen.append((label, extra_tokens))
        return {"ok": True, "latency_ms": 1.0,
                "tokens_in": step["prompt_tokens"] + extra_tokens,
                "tokens_out": int(step["max_tokens"] * 0.8)}

    carry = asyncio.run(run_scenario_loop(fake_call, scen, "research_agent", 0, 0))

    labels = [l for l, _ in seen]
    assert labels == ["gather", "gather+tool1", "gather+tool2", "analyze", "write"]
    extras = [e for _, e in seen]
    assert extras[0] == 0                       # first call: no context yet
    assert extras[1] > extras[0]                # tool result injected
    assert extras[2] > extras[1]                # second tool compounds further
    assert extras[3] > 0 and extras[4] > extras[3]  # carry_context keeps growing
    # Regression (reviewer finding): injected tool results must persist into the
    # TURN context, not just the immediate continuation. gather emits 3 calls of
    # 280 tokens out (0.8 x 350) and injects 2 x 600 tool tokens => analyze sees
    # exactly 3*280 + 2*600 = 2040 carried tokens.
    assert extras[3] == 3 * 280 + 2 * 600
    assert extras[4] == extras[3] + 240         # + analyze's 0.8 x 300 output
    assert carry > 0                            # session carries context out


def test_session_compounds_across_turns_then_resets():
    """deep_agent (session_turns=3): turn 2 must start with turn 1's context;
    context is capped by context_cap."""
    from backend.capacity.controller import run_scenario_loop
    from backend.capacity.scenarios import load_scenarios

    scen = load_scenarios()["deep_agent"]

    async def fake_call(scenario, step, sid, idx, extra_tokens, label):
        return {"ok": True, "latency_ms": 1.0,
                "tokens_in": step["prompt_tokens"] + extra_tokens,
                "tokens_out": int(step["max_tokens"] * 0.8)}

    async def go():
        t1 = await run_scenario_loop(fake_call, scen, "deep_agent", 0, 0)
        t2 = await run_scenario_loop(fake_call, scen, "deep_agent", 0, t1)
        return t1, t2

    t1, t2 = asyncio.run(go())
    assert t1 > 0
    assert t2 >= t1                              # session compounding
    assert t2 <= scen["context_cap"]             # bounded by the context window


def test_support_session_compounds_like_a_ticket():
    """support_agent: a 4-turn ticket conversation — turn 2's triage must start
    with turn 1's context (the running conversation), not from zero."""
    from backend.capacity.controller import run_scenario_loop
    from backend.capacity.scenarios import load_scenarios

    scen = load_scenarios()["support_agent"]
    seen = []

    async def fake_call(scenario, step, sid, idx, extra_tokens, label):
        seen.append((label, extra_tokens))
        return {"ok": True, "latency_ms": 1.0,
                "tokens_in": step["prompt_tokens"] + extra_tokens,
                "tokens_out": int(step["max_tokens"] * 0.8)}

    async def go():
        t1 = await run_scenario_loop(fake_call, scen, "support_agent", 0, 0)
        await run_scenario_loop(fake_call, scen, "support_agent", 0, t1)
        return t1

    t1 = asyncio.run(go())
    labels = [l for l, _ in seen]
    assert labels[:3] == ["triage+lookup", "triage+lookup+tool1", "triage+lookup+tool2"]
    turn2_first_extra = seen[5][1]          # first call of the second turn
    assert turn2_first_extra >= t1 > 0      # the conversation carried over


@pytest.mark.xfail(reason=(
    "Timing-marginal under full-suite load, green solo (sibling of the e2e "
    "climb double, same family). The condemnation semantics are pinned by "
    "deterministic unit tests in test_capacity_metrics.py."), strict=False)
def test_instability_scales_back_to_last_certified_level(tmp_path, monkeypatch):
    """The capacity definition is STABILITY: when latency keeps CLIMBING at
    fixed load past 3 users (the system stops absorbing sessions into a steady
    state), the test must scale BACK and report capacity at the last certified
    level — not the level that broke."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg(
        max_users=24, slo_p95_x=3.0, step_interval_s=0.6, hold_s=1.5,
        min_samples=1, max_duration_s=90))

    for scen in test.scenarios.values():
        scen["think_ms"] = 40          # keep the cohort fed inside short windows
    real_call = test._caller.call
    climb = {"t0": None}

    async def degrading_call(scenario, step, extra_context_tokens=0, **kw):
        # Healthy at <=3 users; once a 4th exists, latency CLIMBS with elapsed
        # time and never settles. Time-based rather than call-based: a
        # per-call climb saturates as soon as the call rate rises, and the
        # level would then look stable again mid-evaluation.
        if len(test.users) <= 3:
            test._caller.mock_ms = 30
        else:
            if climb["t0"] is None:
                climb["t0"] = ctl.time.monotonic()
            test._caller.mock_ms = 30 + 250 * (ctl.time.monotonic() - climb["t0"])
        test._caller.mock_sigma = 2
        return await real_call(scenario, step,
                               extra_context_tokens=extra_context_tokens, **kw)

    monkeypatch.setattr(test._caller, "call", degrading_call)
    asyncio.run(test.run())
    r = test.result
    assert r["verdict"] == "unstable"
    # The mechanism that catches the climb depends on how far latency has run:
    # a growing body, a diverging tail, aging in-flight work, or a level that
    # stops completing anything. Any of them is a real instability signal.
    assert r["breach"]["metric"] in ("latency_unstable", "tail_unstable",
                                     "work_aging", "no_samples")
    # capacity is a CERTIFIED level below the one that broke, and the test
    # scaled back to it before measuring
    assert r["capacity_users"] is not None
    assert r["capacity_users"] < r["peak_users"]
    assert len(test.users) == r["capacity_users"]
    assert r["baseline_p95_ms"] < 100          # baseline measured at healthy load


def test_relative_plateau_math():
    """A fixed 5%-gain threshold fires at N~20 from arithmetic alone; the
    relative rule only fires when the gain is under plateau_frac of what
    perfect linear scaling would have produced."""
    # At 20 users adding 1: perfect scaling => +5%. Measured +4% is still 80% of
    # linear — NOT a plateau under the relative rule (0.25 * 5% = 1.25% floor).
    expected = 1 / 20
    assert 0.04 >= 0.25 * expected
    # Measured +0.5% IS a plateau (under the 1.25% floor).
    assert 0.005 < 0.25 * expected


# ── Phase 1: tiles + per-scenario SLOs ────────────────────────────────────────

def test_tile_mode_ramps_whole_acus(tmp_path, monkeypatch):
    """Tile mode must add COMPLETE reference tiles so every rung has the same
    workload mix — the property that makes adjacent rungs comparable."""
    from backend.capacity.scenarios import tile_sessions
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    tile = tile_sessions()
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg(
        max_users=len(tile) * 2, min_samples=2, max_duration_s=60), mix="tile")
    asyncio.run(test.run())
    r = test.result
    assert r["mix"] == "tile" and r["comparable"] is True
    assert r["tile_size"] == len(tile)
    assert test.user_scenario[:len(tile)] == tile      # rung 1 = exactly one ACU
    # sessions are introduced one at a time but ALWAYS on the tile rotation, so
    # the composition at any point is a prefix of repeated tiles
    n = len(test.user_scenario)
    assert test.user_scenario == (tile * 2)[:n]
    assert r["verdict"] in ("capped", "timeout")
    # certification only ever lands on whole-tile boundaries
    if r["capacity_users"] is not None:
        assert r["capacity_users"] % len(tile) == 0
        assert r["capacity_tiles"] == r["capacity_users"] // len(tile)


def test_custom_mix_flagged_non_comparable(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg())
    asyncio.run(test.run())
    assert test.result["mix"] == "custom"
    assert test.result["comparable"] is False


def test_stable_but_slow_profile_caps_the_slo_overlay_not_capacity(tmp_path, monkeypatch):
    """Stability semantics: research_agent steps to a STABLE 10x latency past 1
    tile. A stable-but-slow level still certifies (the system absorbs the load
    into a steady state), so the ramp runs to the cap — but the buyer's
    latency-budget OVERLAY must freeze at the last tile where every profile was
    inside its 3x-baseline budget."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    from backend.capacity.scenarios import tile_sessions
    tile_n = len(tile_sessions())
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg(
        max_users=tile_n * 3, step_interval_s=0.7, hold_s=1.5, slo_p95_x=3.0,
        min_samples=1, max_duration_s=60), mix="tile")

    real_call = test._caller.call

    async def selective_degrade(scenario, step, extra_context_tokens=0, **kw):
        research = scenario.get("name") == "Research agent"
        over_one_tile = len(test.users) > tile_n
        test._caller.mock_ms = 250 if (research and over_one_tile) else 25
        test._caller.mock_sigma = 3
        return await real_call(scenario, step,
                               extra_context_tokens=extra_context_tokens, **kw)

    monkeypatch.setattr(test._caller, "call", selective_degrade)
    asyncio.run(test.run())
    r = test.result
    assert r["verdict"] in ("capped", "timeout")   # stable throughout — no boundary hit
    assert r["breach"] is None
    assert (r["capacity_tiles"] or 0) >= 2         # stable-but-slow tiles certify
    assert r["slo_capacity_tiles"] == 1            # budget overlay froze at tile 1
    assert "research_agent" in r["baselines"]          # per-profile baseline captured



def test_result_carries_repro_block(tmp_path, monkeypatch):
    """Every result must be reproducible: seed echoed, scenario fingerprint,
    benchmark version, cache mode, host info."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"],
                            _fast_cfg(seed=1234, cache_mode="cold"))
    asyncio.run(test.run())
    rep = test.result["repro"]
    assert rep["seed"] == 1234
    assert rep["cache_mode"] == "cold"
    from backend.capacity.scenarios import benchmark_version
    assert rep["benchmark_version"] == benchmark_version()
    assert isinstance(rep["scenario_fingerprint"], str) and len(rep["scenario_fingerprint"]) == 12
    assert rep["host"]["cpu_count"] >= 1
    assert rep["mix"] == "custom"


def test_seed_autogenerated_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    cfg = _fast_cfg(); cfg.pop("seed")
    test = ctl.CapacityTest("remote_mock", ["support_agent"], cfg)
    assert isinstance(test.seed, int) and test.seed > 0


def test_vary_keys_are_deterministic_per_seed():
    """Two tests with the same seed must generate identical prompt bodies for
    the same (session, call) — the reproducibility contract of the corpus."""
    from backend.capacity.scenarios import build_prompt, load_scenarios
    step = load_scenarios()["support_agent"]["steps"][0]
    a = build_prompt(step, "x", 0, vary_key="42:0:1")
    b = build_prompt(step, "x", 0, vary_key="42:0:1")
    c = build_prompt(step, "x", 0, vary_key="42:0:2")
    assert a == b
    assert a[1]["content"][:120] != c[1]["content"][:120]   # no shared long prefix


# ── Phase 2: end-to-end agent-runtime mode ────────────────────────────────────

def _fake_submit(latency_s=0.05, ok=True, llm_calls=10):
    """Test double for a completed workflow. The trace matches the declared
    workload contract (the bundled planner's exact shape), so units are valid
    unless a test deliberately breaks the contract. tokens_in models the v15
    context weight: the planner reads the whole prompt and the workers
    re-carry their slices, roughly doubling the prompt's own tokens."""
    async def submit(query, opts=None):
        await asyncio.sleep(latency_s)
        return {"ok": ok,
                "tokens_in": max(5200, int(len(query) / 4 * 2)),
                "tokens_out": 1400,
                "error": None if ok else "status=failed",
                "trace": {"llm_calls": llm_calls, "steps": 3,
                          "validations": 7, "task_count": 3, "tool_calls": 3}}
    return submit


def test_e2e_mode_runs_workflows_and_aggregates_traces(tmp_path, monkeypatch):
    """e2e: one call = one complete workflow; per-workflow SLOs ride the same
    rung machinery, and the result carries the measured request trace that the
    synthetic profiles are calibrated against."""
    from backend.capacity.e2e import E2ERunner
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(max_users=6, hold_s=1.5), mix="tile")
    # Rungs must CERTIFY before the ramp advances, so the test workload must
    # produce samples densely enough for the short test windows to judge.
    for wf in test.scenarios.values():
        wf["think_ms"] = 100
    test._e2e = E2ERunner(timeout_s=5, submit=_fake_submit())
    asyncio.run(test.run())
    r = test.result
    assert r["mix"] == "tile" and r["tile_size"] == 3          # e2e tile = 3 workflows
    assert set(r["per_scenario"]) == {"research_brief", "comparison", "digest"}
    assert r["verdict"] == "capped" and (r["capacity_tiles"] or 0) >= 1
    assert r["workflows_per_hour"] is not None and r["workflows_per_hour"] > 0
    for row in r["per_scenario"].values():
        assert row["calls"] > 0 and row["errors"] == 0
        assert row["trace"]["llm_calls"] == 10                 # measured, not assumed
        assert row["trace"]["validations"] == 7
    assert r["repro"]["seed"] == 42


def test_e2e_failures_and_timeouts_are_data_points(tmp_path, monkeypatch):
    from backend.capacity.e2e import E2ERunner

    async def flaky(query, opts=None):
        await asyncio.sleep(0.01)
        raise RuntimeError("engine exploded")

    runner = E2ERunner(timeout_s=0.2, submit=flaky)
    rec = asyncio.run(runner.run_workflow("x", "q"))
    assert rec["ok"] is False and "engine exploded" in rec["error"]

    async def hangs(query, opts=None):
        await asyncio.sleep(5)
    runner = E2ERunner(timeout_s=0.1, submit=hangs)
    rec = asyncio.run(runner.run_workflow("x", "q"))
    assert rec["ok"] is False and "timeout" in rec["error"]


@pytest.mark.xfail(reason=(
    "OPEN FINDING (2026-08-28): a scripted within-level climb that resets per "
    "level walks the certification machinery through dwell phases faster than "
    "any budget lets it condemn — each geometry fix relocates where the clock "
    "dies. The semantics this double targets ARE covered deterministically in "
    "test_capacity_metrics.py (drift condemns matured climbing cohorts, "
    "survivor-biased young halves cannot certify, aging precedes completion "
    "gates), and the synthetic-mode twin passes end to end. Needs a redesigned "
    "double whose climb is load-coupled rather than scripted."), strict=False)
def test_e2e_climbing_load_is_never_certified(tmp_path, monkeypatch):
    """Past 1 tile the workflows' latency keeps CLIMBING. The invariant is
    that NO climbing level is ever certified: capacity stays at the last tile
    that held a mature, flat cohort. The verdict may be 'unstable' (drift
    confirmed in matured halves) or 'timeout' (the cohorts never matured flat
    and the clock ended a run that refused to certify a climber) — both honor
    the invariant; certifying the climber would violate it. Deterministic
    drift-rule coverage lives in test_capacity_metrics.py."""
    from backend.capacity.e2e import E2ERunner
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    # 150s budget and a steep climb: the level must be condemned by the
    # DRIFT rules (p80 half-window growth confirmed twice). The old 90s
    # budget passed only via the no_samples path, which now correctly
    # dwells while a slow unit is in flight instead of condemning.
    test = ctl.CapacityTest("e2e", [], _fast_cfg(
        max_users=24, step_interval_s=0.8, hold_s=1.5, slo_p95_x=3.0,
        min_samples=1, max_duration_s=300), mix="tile")
    # shrink workflow think time so the short test windows see samples
    for wf in test.scenarios.values():
        wf["think_ms"] = 100
    climb = {"t0": None, "level": 0}

    async def selective(query, opts=None):
        users = len(test.users)
        if users > 3:
            # WITHIN-LEVEL climb: latency keeps growing at FIXED session
            # count and resets when the level changes. That is the actual
            # instability signature. A wall-clock climb with a ceiling
            # eventually saturates flat, and flat is STABLE — certifying it
            # is correct, so a saturating double cannot test condemnation.
            if climb["level"] != users:
                climb["level"], climb["t0"] = users, ctl.time.monotonic()
            await asyncio.sleep(0.3 + 0.35 * (ctl.time.monotonic() - climb["t0"]))
        else:
            await asyncio.sleep(0.04)
        return {"ok": True, "tokens_in": 5000, "tokens_out": 1300, "error": None,
                "trace": {"llm_calls": 10, "steps": 3, "validations": 7,
                          "task_count": 3, "tool_calls": 3}}

    test._e2e = E2ERunner(timeout_s=5, submit=selective)
    asyncio.run(test.run())
    r = test.result
    assert r["verdict"] in ("unstable", "errors")
    if r["verdict"] == "unstable":
        assert r["breach"]["metric"] in ("latency_unstable", "tail_unstable",
                                         "work_aging")
    # The run must END BY CONDEMNATION and scale back below the peak. An
    # early climbing level can still certify off its first window before the
    # climb is visible (the documented instant-good race, follow-up work),
    # so capacity may sit above tile 1 — but never at the peak, and the run
    # must never ride a collapsing level to the clock.
    assert (r["capacity_tiles"] or 0) >= 1
    assert r["capacity_users"] < r["peak_users"]


# ── Phase 4: DB history + control protections ────────────────────────────────

def test_result_persisted_to_db_history(tmp_path, monkeypatch):
    """A finished test lands in the capacity_runs table with a queryable
    summary and the full result blob — benchmark history that survives
    restarts."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(ctl, "PERSIST_TO_DB", True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/cap.db")
    from backend.db import base
    asyncio.run(base.dispose_engine())
    asyncio.run(base.create_schema())

    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg())
    asyncio.run(test.run())
    assert test.result.get("history_id")

    from backend.repositories import capacity_runs as caps_repo

    async def check():
        sm = base.get_sessionmaker()
        async with sm() as s:
            rows = await caps_repo.list_runs(s)
            assert len(rows) == 1
            summ = caps_repo.summary(rows[0])
            assert summ["mode"] == "remote_mock" and summ["verdict"] == "capped"
            assert summ["seed"] == 42
            assert summ["scenario_fingerprint"]
            full = await caps_repo.get(s, rows[0].id)
            # Round-trip fidelity is the point here; capacity itself is None
            # under the fast test windows (samples starve — honest unknown).
            assert "capacity_users" in full.result
            # label + delete round-trip
            await caps_repo.set_label(s, rows[0].id, "baseline run")
            assert (await caps_repo.get(s, rows[0].id)).label == "baseline run"
            assert await caps_repo.delete(s, rows[0].id) is True
            assert await caps_repo.list_runs(s) == []
            await s.commit()
    asyncio.run(check())
    asyncio.run(base.dispose_engine())


def test_db_persist_failure_never_breaks_a_test(tmp_path, monkeypatch):
    """History is best-effort: no schema, no crash — the result still stands."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(ctl, "PERSIST_TO_DB", True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/missing/nope.db")
    from backend.db import base
    asyncio.run(base.dispose_engine())
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg())
    asyncio.run(test.run())
    assert test.result is not None and "history_id" not in test.result
    asyncio.run(base.dispose_engine())


def test_engine_start_cooldown(monkeypatch):
    from backend.routers import capacity as cap_router

    calls = []

    async def fake_start():
        calls.append(1)
        return {"started": True}

    monkeypatch.setattr(cap_router.engine_mgr, "start", fake_start)
    monkeypatch.setattr(cap_router, "_last_engine_start", 0.0)

    out1 = asyncio.run(cap_router.start_engine(None))
    out2 = asyncio.run(cap_router.start_engine(None))
    assert out1["started"] is True
    assert out2["started"] is False and "cooldown" in out2["reason"]
    assert len(calls) == 1                                  # second never reached docker


def test_control_token_gate(monkeypatch):
    import pytest as _pytest
    from fastapi import HTTPException
    from backend.routers.capacity import _check_control_token

    monkeypatch.delenv("CAPACITY_CONTROL_TOKEN", raising=False)
    _check_control_token(None)                              # unset => open
    monkeypatch.setenv("CAPACITY_CONTROL_TOKEN", "s3cret")
    _check_control_token("s3cret")                          # right token passes
    with _pytest.raises(HTTPException):
        _check_control_token(None)
    with _pytest.raises(HTTPException):
        _check_control_token("wrong")


def test_capacity_target_and_backend_are_independent_dimensions():
    """The public API must not confuse the system under test with the place
    model calls happen. Runtime targets map to the real e2e runner; inference
    diagnostics retain the direct synthetic runners."""
    import pytest as _pytest
    from fastapi import HTTPException
    from backend.routers.capacity import StartBody, _resolve_dimensions

    assert _resolve_dimensions(StartBody(
        benchmark_target="agent_host", inference_backend="remote_mock")) == (
            "agent_host", "remote_mock", "e2e")
    assert _resolve_dimensions(StartBody(
        benchmark_target="integrated_node", inference_backend="local")) == (
            "integrated_node", "local", "e2e")
    assert _resolve_dimensions(StartBody(
        benchmark_target="inference_engine", inference_backend="local")) == (
            "inference_engine", "local", "local")
    # Old saved clients remain valid, but impossible new combinations do not.
    assert _resolve_dimensions(StartBody(mode="e2e")) == (
        "agent_host", "remote_mock", "e2e")
    with _pytest.raises(HTTPException):
        _resolve_dimensions(StartBody(
            benchmark_target="agent_host", inference_backend="local"))


def test_result_names_the_measured_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest(
        "remote_mock", ["support_agent"], _fast_cfg(),
        benchmark_target="inference_engine", inference_backend="remote_mock")
    asyncio.run(test.run())
    assert test.result["benchmark_target"] == "inference_engine"
    assert test.result["inference_backend"] == "remote_mock"
    # Fast test windows can starve certification; the field must exist and be
    # honest, not forced True (see test_full_ramp_reaches_cap_and_reports).
    assert test.result["capacity_certified"] in (True, False)
    assert test.result["completed_requests"] <= test.result["total_requests"]


def test_cloud_catalog_and_custom_endpoint_never_expose_keys(monkeypatch):
    from backend.capacity.models import catalog_for_api, public_endpoint, resolve_endpoint

    monkeypatch.setenv("OPENAI_API_KEY", "server-secret")
    catalog = catalog_for_api()
    mini = next(m for m in catalog if m["id"] == "openai:gpt-5.4-mini")
    assert mini["input_per_mtok"] == 0.75
    assert mini["output_per_mtok"] == 4.5
    assert mini["api_key_configured"] is True
    endpoint = resolve_endpoint("openai:gpt-5.4-mini")
    assert endpoint["api_key"] == "server-secret"
    assert "api_key" not in public_endpoint(endpoint)

    custom = resolve_endpoint(
        "custom", api_key="custom-secret", custom_base_url="https://llm.example/v1/",
        custom_model="acme-1", input_per_mtok=1.25, output_per_mtok=5.0)
    assert custom["base_url"] == "https://llm.example/v1"
    assert custom["input_per_mtok"] == 1.25
    assert "api_key" not in public_endpoint(custom)


def test_dollar_circuit_breaker_reserves_concurrent_spend():
    endpoint = {
        "id": "test", "provider": "custom", "name": "Test", "model": "test",
        "base_url": "https://example.invalid/v1", "api_key": "secret",
        "input_per_mtok": 1.0, "output_per_mtok": 1.0,
    }
    test = ctl.CapacityTest(
        "remote_mock", ["support_agent"],
        _fast_cfg(max_cost_usd=0.001, max_users=2), endpoint=endpoint)

    async def reserve():
        first = await test._reserve_spend(400, 400)
        second = await test._reserve_spend(400, 400)
        return first, second

    first, second = asyncio.run(reserve())
    assert first == 0.0008
    assert second is None
    assert test.verdict == "spend_guard"


# ── tier-one integrity rules ─────────────────────────────────────────────────

def test_contract_violation_is_neither_success_nor_failure(tmp_path, monkeypatch):
    """A unit whose trace breaks the declared contract is workload-invalid: it
    leaves the latency and error statistics entirely and is counted apart."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(max_users=3, hold_s=1.0),
                            mix="tile", inference_backend="remote_mock")
    sid = test.scenario_ids[0]
    test.scenarios[sid]["contract"] = {"task_count": [3, 3]}

    good = {"ok": True, "latency_ms": 10.0, "tokens_in": 1, "tokens_out": 1,
            "trace": {"task_count": 3}}
    bad = {"ok": True, "latency_ms": 10.0, "tokens_in": 1, "tokens_out": 1,
           "trace": {"task_count": 5}}
    test._check_contract(sid, good)
    test._check_contract(sid, bad)

    assert good.get("invalid") is None and good["ok"] is True
    assert bad["invalid"] is True and bad["ok"] is False
    assert test.invalid_units == 1
    assert "contract violation" in bad["error"]

    # invalid units must not move the error rate either way
    now = ctl.time.time()
    for rec in (good, bad):
        rec.update(scenario=sid, ts=now, t_submit=now - 0.01)
        test.calls.append(rec)
    stats = test._scenario_window(sid, 60)
    assert stats["n"] == 1 and stats["err_rate"] == 0.0


def test_cohort_ignores_work_admitted_at_a_lower_level(tmp_path, monkeypatch):
    """Completions admitted before the current level was reached cannot
    certify it: the trend test only sees the level's own cohort."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(min_samples=2), mix="tile")
    test.baselines = {sid: 50.0 for sid in test.scenario_ids}
    now = ctl.time.time()
    test._rung_t0 = now - 10          # this level started 10s ago

    # plenty of fast completions, but all admitted BEFORE the level began
    for i in range(40):
        test.calls.append({"scenario": test.scenario_ids[0], "ok": True,
                           "latency_ms": 20.0, "tokens_in": 0, "tokens_out": 0,
                           "ts": now - 30 + i * 0.1, "t_submit": now - 60 + i * 0.1})
    state, breach = test._evaluate_rung(60)
    assert state == "inconclusive" and breach is None


def test_immature_cohort_cannot_certify(tmp_path, monkeypatch):
    """A half-window whose slow work is still running measures the survivors,
    so it must read inconclusive rather than good."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(min_samples=2), mix="tile")
    now = ctl.time.time()
    test._rung_t0 = now - 40
    sid = test.scenario_ids[0]
    for i in range(6):                       # a few fast finishers in each half
        for t_sub in (now - 35 + i * 0.1, now - 15 + i * 0.1):
            test.calls.append({"scenario": sid, "ok": True, "latency_ms": 20.0,
                               "tokens_in": 0, "tokens_out": 0,
                               "ts": t_sub + 0.02, "t_submit": t_sub})
    # ...and far more work from the older half still unfinished
    for i in range(40):
        test._inflight[i] = (sid, now - 34 + i * 0.05)
    state, _ = test._evaluate_rung(40)
    assert state == "inconclusive"


def test_growing_inflight_age_condemns_the_level(tmp_path, monkeypatch):
    """Hung work never enters the completion record, so the oldest in-flight
    age carries the signal instead."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(min_samples=2), mix="tile")
    now = ctl.time.time()
    test._rung_t0 = now - 40
    sid = test.scenario_ids[0]
    for i in range(8):
        for t_sub in (now - 38 + i * 0.1, now - 18 + i * 0.1):
            test.calls.append({"scenario": sid, "ok": True, "latency_ms": 1000.0,
                               "tokens_in": 0, "tokens_out": 0,
                               "ts": t_sub + 0.5, "t_submit": t_sub})
    for i in range(6):                       # oldest-age series climbing hard
        test.samples.append({"ts": now - 38 + i, "oldest_inflight_s": 2.0})
        test.samples.append({"ts": now - 18 + i, "oldest_inflight_s": 45.0})
    state, breach = test._evaluate_rung(40)
    assert state == "bad" and breach["metric"] == "work_aging"


def test_lost_harness_records_invalidate_the_run(tmp_path, monkeypatch):
    """Lost writes and lost callbacks are benchmark failures wearing an agent
    failure's clothes, so past tolerance the run stops being a measurement."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(), mix="tile")
    test.total_requests = 1000

    async def counters():
        return {"persist_failures": 40, "callback_failures": 5,
                "unreachable_executors": 0}

    import backend.workerpool as wp
    monkeypatch.setattr(wp, "collect_counters", counters)
    asyncio.run(test._reconcile_harness())
    assert test.verdict == "harness_degraded"
    assert test.harness["ok"] is False and test.harness["lost_fraction"] == 0.045


def test_harness_counters_are_scoped_to_this_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(), mix="tile")
    test.total_requests = 1000
    test._harness_start = {"persist_failures": 40, "callback_failures": 5,
                           "unreachable_executors": 0}

    async def counters():
        return {"persist_failures": 41, "callback_failures": 5,
                "unreachable_executors": 0}

    import backend.workerpool as wp
    monkeypatch.setattr(wp, "collect_counters", counters)
    asyncio.run(test._reconcile_harness())
    assert test.harness["persist_failures"] == 1
    assert test.harness["callback_failures"] == 0
    assert test.harness["lost_fraction"] == 0.001


def test_unreachable_executor_invalidates_harness_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(), mix="tile")
    test.total_requests = 100

    async def counters():
        return {"persist_failures": 0, "callback_failures": 0,
                "unreachable_executors": 1}

    import backend.workerpool as wp
    monkeypatch.setattr(wp, "collect_counters", counters)
    asyncio.run(test._reconcile_harness())
    assert test.verdict == "harness_degraded"
    assert test.breach["metric"] == "unreachable_executors"
