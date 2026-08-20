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
    assert len(r["per_scenario"]) == 6
    assert r["steady"]["p50_ms"] is not None
    # memory-telemetry fields are always present (None where unmeasurable)
    assert "bw_gbs" in r["steady"] and "kv_pct" in r["steady"]
    assert "mem_mb_per_user" in r
    assert all("avg_kv_tokens" in s for s in r["per_scenario"].values())
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
