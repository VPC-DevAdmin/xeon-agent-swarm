"""Workload v17: sandboxed tool execution."""
import asyncio

import pytest

from backend.capacity import sandbox


def test_light_job_runs_isolated_and_is_deterministic(monkeypatch):
    monkeypatch.setenv("CAPACITY_SANDBOX_ISOLATION", "rlimits")
    sandbox._mode = None
    a = asyncio.run(sandbox.run_job("light", 7))
    b = asyncio.run(sandbox.run_job("light", 7))
    assert a["ok"] and a["rows"] == sandbox.SIZES["light"]
    assert a["top_keys"] == b["top_keys"] and a["q95"] == b["q95"]
    assert a["isolation"] == "rlimits" and a["cpu_ms"] > 0


def test_unknown_size_is_refused():
    with pytest.raises(ValueError):
        asyncio.run(sandbox.run_job("enormous", 1))


def test_wall_limit_kills_a_runaway(monkeypatch):
    monkeypatch.setenv("CAPACITY_SANDBOX_ISOLATION", "rlimits")
    sandbox._mode = None
    monkeypatch.setattr(sandbox, "WALL_LIMIT_S", 0.05)
    r = asyncio.run(sandbox.run_job("heavy", 3))
    assert r["ok"] is False and r["error"] == "wall limit"


def test_execute_tool_returns_results_into_context(monkeypatch):
    monkeypatch.setenv("CAPACITY_SANDBOX_ISOLATION", "rlimits")
    sandbox._mode = None
    from backend.agents.toolbox import build_bench_execute_tool
    tool = build_bench_execute_tool()
    out = asyncio.run(tool.coroutine(task="profile the data", size="light"))
    assert out.startswith(f"[bench_execute] light job over {sandbox.SIZES['light']:,} rows")
    assert "EXECUTION COMPLETE" in out and "p95" in out
