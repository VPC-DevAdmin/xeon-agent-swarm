"""
Offline test for the ADL_ENGINE dispatch in launch_run.

No gateway, no DB: the two runners are stubbed so we assert only that launch_run
routes to the right engine for the env flag and threads its arguments through.
The deepagents runner's end-to-end behavior is covered by the event-adapter and
judge tests; here we lock in the swarm⇄deepagents switch that keeps the old path
reachable until cutover.
"""
from __future__ import annotations

import asyncio

import backend.main as m


def _launch_under(monkeypatch, engine: str) -> dict:
    called: dict = {}

    async def fake_swarm(run_id, query, **kw):
        called.update(engine="swarm", run_id=run_id, query=query, kw=kw)

    async def fake_deep(run_id, query, **kw):
        called.update(engine="deepagents", run_id=run_id, query=query, kw=kw)

    monkeypatch.setattr(m, "run_swarm", fake_swarm)
    monkeypatch.setattr(m, "run_deepagents", fake_deep)
    monkeypatch.setenv("ADL_ENGINE", engine)

    async def go():
        rid = m.launch_run("compare X and Y", validator_enabled=True, trigger="manual")
        # let the background task created by launch_run run to completion
        for _ in range(3):
            await asyncio.sleep(0)
        return rid

    rid = asyncio.run(go())
    called["returned_run_id"] = rid
    return called


def test_launch_run_routes_to_deepagents(monkeypatch):
    called = _launch_under(monkeypatch, "deepagents")
    assert called["engine"] == "deepagents"
    assert called["run_id"] == called["returned_run_id"]
    assert called["query"] == "compare X and Y"
    assert called["kw"]["validator_enabled"] is True


def test_launch_run_defaults_to_swarm(monkeypatch):
    monkeypatch.delenv("ADL_ENGINE", raising=False)
    called: dict = {}

    async def fake_swarm(run_id, query, **kw):
        called["engine"] = "swarm"

    async def fake_deep(run_id, query, **kw):
        called["engine"] = "deepagents"

    monkeypatch.setattr(m, "run_swarm", fake_swarm)
    monkeypatch.setattr(m, "run_deepagents", fake_deep)

    async def go():
        m.launch_run("q")
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert called["engine"] == "swarm"          # default when ADL_ENGINE unset


def test_launch_run_swarm_explicit(monkeypatch):
    called = _launch_under(monkeypatch, "swarm")
    assert called["engine"] == "swarm"
