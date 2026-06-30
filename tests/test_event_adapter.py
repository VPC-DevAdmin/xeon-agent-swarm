"""
Offline unit test for the deepagents → app event adapter.

Feeds a synthetic deepagents stream (the exact (namespace, mode, chunk) shape
verified against deepagents 0.6.10) through EventAdapter with a fake persistence
layer — no gateway, no DB, fast and deterministic. Locks in the structural
contract the adapter depends on so later stages don't silently break it.
"""
from __future__ import annotations

import asyncio

from backend.observability.event_adapter import EventAdapter
from backend.schemas.models import EventType


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeMsg:
    """Mimics a LangChain message as the adapter reads it. The adapter switches on
    type(m).__name__, so the concrete subclasses below carry the right names."""
    def __init__(self, *, tier=None, tokens=(0, 0), tool_calls=None,
                 content="", name=None, tool_call_id=None, category=None):
        self.content = content
        self.name = name
        self.tool_call_id = tool_call_id
        self.tool_calls = tool_calls or []
        self.usage_metadata = {"input_tokens": tokens[0], "output_tokens": tokens[1]}
        headers = {}
        if tier is not None:
            headers["x-vsr-selected-model"] = tier          # present => not a cache hit
        if category:
            headers["x-vsr-selected-category"] = category
        self.response_metadata = {"headers": headers, "model_name": tier}


class AIMessage(FakeMsg):
    pass


class ToolMessage(FakeMsg):
    pass


def ai(**kw):
    return AIMessage(**kw)


def tool(**kw):
    return ToolMessage(**kw)


class FakeDB:
    def __init__(self):
        self.steps = {}
        self.attempts = []
        self.validations = []
        self.run = {}

    async def create_step(self, run_id, *, step_key, type, **kw):
        self.steps.setdefault(step_key, {"type": type, "status": "running", **kw})

    async def set_step_status(self, run_id, step_key, status, **kw):
        self.steps.setdefault(step_key, {}).update(status=status, **kw)

    async def record_attempt(self, run_id, step_key, **kw):
        self.attempts.append({"step_key": step_key, **kw})

    async def record_validation(self, run_id, step_key, *, level, verdict, **kw):
        self.validations.append({"step_key": step_key, "level": level,
                                 "verdict": verdict, **kw})

    async def finalize_run(self, run_id, **kw):
        self.run = kw


def _synthetic_stream(t1, t2):
    """Reproduce the verified deepagents stream for a 2-delegation run.

    t1/t2 are the task tool_call ids for the research and writing delegations.
    """
    return [
        # planner delegates research (and is itself a T5 call)
        ((), "updates", {"model": {"messages": [
            ai(tier="tier5", tokens=(9000, 500),
               tool_calls=[{"name": "task", "id": t1,
                            "args": {"subagent_type": "research", "description": "gather facts"}}])]}}),
        # research worker internal calls (routed tier5)
        (("tools:AAA",), "updates", {"model": {"messages": [ai(tier="tier5", tokens=(6000, 230))]}}),
        (("tools:AAA",), "updates", {"model": {"messages": [ai(tier="tier5", tokens=(7000, 256))]}}),
        # research delegation closes — result is a valid research JSON envelope
        ((), "updates", {"tools": {"messages": [tool(
            name="task", tool_call_id=t1,
            content='{"result": "vLLM hits 2400 req/s vs llama.cpp 100 req/s, a 24x gap across the board.", "confidence": 0.8}')]}}),
        # planner delegates writing
        ((), "updates", {"model": {"messages": [
            ai(tier="tier5", tokens=(10000, 900),
               tool_calls=[{"name": "task", "id": t2,
                            "args": {"subagent_type": "writing", "description": "write it up"}}])]}}),
        # writing worker routed cheaper (tier3)
        (("tools:BBB",), "updates", {"model": {"messages": [ai(tier="tier3", tokens=(4000, 256))]}}),
        # writing delegation closes — prose result (writing is not a JSON role)
        ((), "updates", {"tools": {"messages": [tool(
            name="task", tool_call_id=t2,
            content="A clear, sufficiently long write-up comparing the two engines. " * 3)]}}),
        # synthesis: planner composes final answer, no tool calls
        ((), "updates", {"model": {"messages": [ai(tier="tier5", tokens=(12000, 600),
                                                   content="# Final brief\nvLLM vs llama.cpp ...")]}}),
    ]


def test_adapter_builds_full_telemetry():
    async def run():
        fdb = FakeDB()
        adapter = EventAdapter("run-x", broadcast=None, persistence=fdb, planner_tier="T5")
        await adapter.start("compare vLLM and llama.cpp")
        for ns, mode, chunk in _synthetic_stream("tool_t1", "tool_t2"):
            await adapter.handle(ns, mode, chunk)
        summary = await adapter.finalize()
        return fdb, adapter, summary

    fdb, adapter, summary = asyncio.run(run())

    # Steps: orchestrator + one per delegation
    assert "orchestrator" in fdb.steps
    assert "research-1" in fdb.steps
    assert "writing-2" in fdb.steps
    assert fdb.steps["research-1"]["status"] == "completed"

    # Attempts: planner pinned T5; worker calls tagged auto with their routed tier
    planner = [a for a in fdb.attempts if a["tier_requested"] == "T5"]
    workers = [a for a in fdb.attempts if a["tier_requested"] == "auto"]
    assert len(planner) == 3          # 2 delegating turns + 1 synthesis turn
    assert len(workers) == 3          # 2 research calls + 1 writing call
    assert {a["tier_observed"] for a in workers} == {"T5", "T3"}

    # Validation: one L0 row per delegation; the writing prose passes, research JSON passes
    assert len(fdb.validations) == 2
    assert all(v["level"] == "mechanical" for v in fdb.validations)

    # Cost rollup populated; baseline (all-T5) >= total since one worker ran T3
    assert summary["cost"]["call_count"] == 6      # 3 planner + 3 worker calls
    assert fdb.run["total_cost"] <= fdb.run["baseline_cost"]
    assert fdb.run["savings_pct"] >= 0.0

    # Final answer captured from the synthesis turn
    assert "Final brief" in (summary["final_answer"] or "")

    # Event vocabulary emitted
    emitted = {e.event for e in adapter.events}
    for expected in (EventType.run_started, EventType.task_started,
                     EventType.validator_approved, EventType.task_completed,
                     EventType.synthesis_started, EventType.run_completed,
                     EventType.run_metrics):
        assert expected in emitted, expected


if __name__ == "__main__":
    test_adapter_builds_full_telemetry()
    print("test_event_adapter OK")
