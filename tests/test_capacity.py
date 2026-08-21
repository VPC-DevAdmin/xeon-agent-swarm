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
               max_duration_s=30, plateau_frac=0, warmup_s=0, seed=42)
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
    assert r["capacity_users"] == 3          # SLO held the whole way => capacity = held level
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


def test_slo_breach_scales_back_to_last_good_level(tmp_path, monkeypatch):
    """The capacity-planning definition: when latency blows past the SLO at 4
    users, the test must scale BACK to 3 and report capacity_users=3 — the level
    a customer could actually run at — not the level that broke."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg(
        max_users=10, slo_p95_x=3.0, step_interval_s=0.6, hold_s=1.5))

    real_call = test._caller.call

    async def degrading_call(scenario, step, extra_context_tokens=0, **kw):
        # Healthy at <=3 users; latency x10 once the 4th user exists.
        test._caller.mock_ms = 30 if len(test.users) <= 3 else 300
        test._caller.mock_sigma = 2
        return await real_call(scenario, step,
                               extra_context_tokens=extra_context_tokens, **kw)

    monkeypatch.setattr(test._caller, "call", degrading_call)
    asyncio.run(test.run())
    r = test.result
    assert r["verdict"] == "slo"
    assert r["capacity_users"] == 3            # last good level, not the breach level
    assert len(test.users) == 3                # scaled back down before the hold
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
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg(max_users=len(tile) * 2),
                            mix="tile")
    asyncio.run(test.run())
    r = test.result
    assert r["mix"] == "tile" and r["comparable"] is True
    assert r["tile_size"] == len(tile)
    # sessions were added in whole tiles only
    assert r["max_users"] % len(tile) == 0
    assert test.user_scenario[:len(tile)] == tile      # rung 1 = exactly one ACU
    assert r["verdict"] == "capped"
    assert r["capacity_tiles"] == r["capacity_users"] // len(tile)


def test_custom_mix_flagged_non_comparable(tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("remote_mock", ["support_agent"], _fast_cfg())
    asyncio.run(test.run())
    assert test.result["mix"] == "custom"
    assert test.result["comparable"] is False


def test_per_profile_slo_breach_names_the_profile(tmp_path, monkeypatch):
    """Only research_agent degrades past 1 tile; the rung must fail on THAT
    profile's own baseline-relative SLO while others stay healthy, and the
    breach must name it — 'tile N+1 failed the research-agent p95 SLO'."""
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    from backend.capacity.scenarios import tile_sessions
    tile_n = len(tile_sessions())
    test = ctl.CapacityTest("remote_mock", [], _fast_cfg(
        max_users=tile_n * 4, step_interval_s=0.7, hold_s=1.5, slo_p95_x=3.0),
        mix="tile")

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
    assert r["verdict"] == "slo"
    assert r["breach"]["profile"] == "research_agent"
    assert r["breach"]["metric"] == "p95_ms"
    assert r["breach"]["value"] > r["breach"]["limit"]
    assert r["capacity_tiles"] == 1                    # last rung where ALL SLOs held
    assert len(test.users) == tile_n                   # scaled back to the good tile
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
    assert rep["benchmark_version"] == 2
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

def _fake_submit(latency_s=0.05, ok=True, llm_calls=7):
    async def submit(query, opts=None):
        await asyncio.sleep(latency_s)
        return {"ok": ok, "tokens_in": 5200, "tokens_out": 1400,
                "error": None if ok else "status=failed",
                "trace": {"llm_calls": llm_calls, "steps": 3,
                          "validations": 4, "task_count": 3}}
    return submit


def test_e2e_mode_runs_workflows_and_aggregates_traces(tmp_path, monkeypatch):
    """e2e: one call = one complete workflow; per-workflow SLOs ride the same
    rung machinery, and the result carries the measured request trace that the
    synthetic profiles are calibrated against."""
    from backend.capacity.e2e import E2ERunner
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(max_users=6, hold_s=1.5), mix="tile")
    test._e2e = E2ERunner(timeout_s=5, submit=_fake_submit())
    asyncio.run(test.run())
    r = test.result
    assert r["mix"] == "tile" and r["tile_size"] == 3          # e2e tile = 3 workflows
    assert set(r["per_scenario"]) == {"research_brief", "comparison", "digest"}
    assert r["verdict"] == "capped" and r["capacity_tiles"] == 2
    assert r["workflows_per_hour"] is not None and r["workflows_per_hour"] > 0
    for row in r["per_scenario"].values():
        assert row["calls"] > 0 and row["errors"] == 0
        assert row["trace"]["llm_calls"] == 7                  # measured, not assumed
        assert row["trace"]["validations"] == 4
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


def test_e2e_slo_breach_names_the_workflow(tmp_path, monkeypatch):
    """Only the digest workflow degrades past 1 tile — the breach must name it
    and capacity must report the last all-green tile."""
    from backend.capacity.e2e import E2ERunner
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)
    test = ctl.CapacityTest("e2e", [], _fast_cfg(
        max_users=9, step_interval_s=0.8, hold_s=1.5, slo_p95_x=3.0,
        min_samples=1), mix="tile")
    # shrink workflow think time so the short test windows see samples
    for wf in test.scenarios.values():
        wf["think_ms"] = 100

    async def selective(query, opts=None):
        digest = "digest" in query.lower() or "bullet" in query.lower()
        slow = digest and len(test.users) > 3
        await asyncio.sleep(0.5 if slow else 0.04)
        return {"ok": True, "tokens_in": 5000, "tokens_out": 1300, "error": None,
                "trace": {"llm_calls": 7, "steps": 3, "validations": 4, "task_count": 3}}

    test._e2e = E2ERunner(timeout_s=5, submit=selective)
    asyncio.run(test.run())
    r = test.result
    assert r["verdict"] == "slo"
    assert r["breach"]["profile"] == "digest"
    assert r["capacity_tiles"] == 1
    assert len(test.users) == 3                                # scaled back to 1 tile


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
            assert full.result["capacity_users"] == 3
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
    assert test.result["capacity_certified"] is True
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
