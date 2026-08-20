"""AgentDefinition: CRUD, versioning, cloning, schedule sync, and assignment
into the e2e benchmark mix. Uses a throwaway sqlite file — no live services."""
from __future__ import annotations

import asyncio
import os

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/defs.db")
    from backend.db import base
    asyncio.run(base.dispose_engine())          # drop any engine bound to old URL
    asyncio.run(base.create_schema())
    yield base
    asyncio.run(base.dispose_engine())


def _mk(**over):
    fields = dict(name="Morning digest agent", icon="📰",
                  purpose="Daily digest with verification",
                  instructions="Compile a digest of AI serving news and verify claims.",
                  enabled_tools=["web_search", "telegram"],
                  plan_approval=False, validator_enabled=True,
                  budgets={"max_subagents": 4}, session_policy={"turns": 2},
                  slo={"p95_ms": 60_000}, schedule_cron=None, schedule_tz="UTC")
    fields.update(over)
    return fields


def test_create_update_versions_and_clone(db):
    from backend.repositories import agent_defs as repo

    async def go():
        sm = db.get_sessionmaker()
        async with sm() as s:
            d = await repo.create(s, **_mk())
            assert d.version == 1 and d.status == "active"
            d = await repo.update(s, d.id, {"instructions": "Wider digest scope.",
                                            "enabled_tools": ["web_search"]})
            assert d.version == 2
            assert d.history[-1]["version"] == 1                    # prior state kept
            assert d.history[-1]["snapshot"]["enabled_tools"] == ["web_search", "telegram"]
            c = await repo.clone(s, d.id)
            assert c.name == "Morning digest agent (copy)"
            assert c.version == 1 and c.instructions == "Wider digest scope."
            assert c.schedule_cron is None                          # clones never inherit schedules
            await s.commit()
    asyncio.run(go())


def test_schedule_sync_creates_and_archives_job(db):
    from backend.repositories import agent_defs as repo
    from backend.repositories import jobs as jobs_repo

    async def go():
        sm = db.get_sessionmaker()
        async with sm() as s:
            d = await repo.create(s, **_mk(schedule_cron="0 9 * * *"))
            assert d.job_id is not None
            job = await jobs_repo.get_job(s, d.job_id)
            assert job.name == "[agent] Morning digest agent"
            assert job.config["agent_definition_id"] == d.id
            assert job.config["enabled_tools"] == ["web_search", "telegram"]
            assert job.config["budget"] == {"max_subagents": 4}     # scheduler passes this
            # clearing the schedule archives the linked job
            d = await repo.update(s, d.id, {"schedule_cron": None})
            assert d.job_id is None
            await s.commit()
    asyncio.run(go())


def test_archive_definition_archives_job(db):
    from backend.repositories import agent_defs as repo
    from backend.repositories import jobs as jobs_repo

    async def go():
        sm = db.get_sessionmaker()
        async with sm() as s:
            d = await repo.create(s, **_mk(name="Sched agent", schedule_cron="0 * * * *"))
            jid = d.job_id
            await repo.archive(s, d.id)
            job = await jobs_repo.get_job(s, jid)
            assert job.status == "archived"
            defs = await repo.list_defs(s)
            assert all(x.id != d.id for x in defs)                  # hidden by default
            await s.commit()
    asyncio.run(go())


def test_definitions_join_the_e2e_benchmark_mix(tmp_path, monkeypatch):
    """A definition assigned to the benchmark becomes an e2e workflow with its
    own policy carried through to the runner."""
    from backend.capacity import controller as ctl
    from backend.capacity.e2e import E2ERunner
    monkeypatch.setattr(ctl, "RESULTS_DIR", tmp_path)

    seen_opts = {}

    async def submit(query, opts=None):
        seen_opts.update(opts or {})
        await asyncio.sleep(0.02)
        return {"ok": True, "tokens_in": 900, "tokens_out": 300, "error": None,
                "trace": {"llm_calls": 4, "steps": 2, "validations": 2, "task_count": 2}}

    extra = {"def:abc12345": {"name": "📰 Morning digest agent (v3)",
                              "query": "Compile the digest.", "think_ms": 100,
                              "enabled_tools": ["web_search"],
                              "validator_enabled": True,
                              "budgets": {"max_subagents": 4}}}
    cfg = dict(mock_ms=25, mock_sigma=4, step_interval_s=0.4, hold_s=1.5,
               sample_interval_s=0.1, max_users=2, start_users=1, step_users=1,
               max_duration_s=20, plateau_frac=0, warmup_s=0, seed=7, min_samples=1)
    test = ctl.CapacityTest("e2e", ["def:abc12345"], cfg, mix="custom",
                            extra_workflows=extra)
    test._e2e = E2ERunner(timeout_s=5, submit=submit)
    asyncio.run(test.run())
    r = test.result
    assert "def:abc12345" in r["per_scenario"]
    assert r["per_scenario"]["def:abc12345"]["calls"] > 0
    assert seen_opts["enabled_tools"] == ["web_search"]             # policy reached the runner
    assert seen_opts["budgets"] == {"max_subagents": 4}
    assert r["comparable"] is False                                 # custom mix stays honest
