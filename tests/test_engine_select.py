"""
Offline test for launch_run after the cutover.

The old swarm engine was removed; launch_run now always runs the deepagents
engine and registers the run task for /kill. A stale ADL_ENGINE=swarm logs a
warning but still runs deepagents (never silently does nothing).
"""
from __future__ import annotations

import asyncio

import backend.main as m


def _launch_under(monkeypatch, engine: str | None) -> dict:
    called: dict = {}

    async def fake_deep(run_id, query, **kw):
        called.update(run_id=run_id, query=query, kw=kw)

    monkeypatch.setattr(m, "run_deepagents", fake_deep)
    if engine is None:
        monkeypatch.delenv("ADL_ENGINE", raising=False)
    else:
        monkeypatch.setenv("ADL_ENGINE", engine)

    async def go():
        rid = m.launch_run("compare X and Y", validator_enabled=True, trigger="manual")
        for _ in range(3):
            await asyncio.sleep(0)
        return rid

    called["returned_run_id"] = asyncio.run(go())
    return called


def test_launch_run_runs_deepagents_by_default(monkeypatch):
    called = _launch_under(monkeypatch, None)
    assert called["run_id"] == called["returned_run_id"]
    assert called["query"] == "compare X and Y"
    assert called["kw"]["validator_enabled"] is True


def test_launch_run_explicit_deepagents(monkeypatch):
    called = _launch_under(monkeypatch, "deepagents")
    assert called["run_id"] == called["returned_run_id"]


def test_stale_swarm_value_still_runs_deepagents(monkeypatch):
    # A leftover ADL_ENGINE=swarm must not silently no-op — it runs deepagents.
    called = _launch_under(monkeypatch, "swarm")
    assert called["run_id"] == called["returned_run_id"]
    assert called["query"] == "compare X and Y"
