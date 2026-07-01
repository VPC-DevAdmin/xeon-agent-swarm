"""
Offline tests for Stage 5: the L2 synthesis grader and per-run budget stops.

No gateway: a fake synthesis grader returns a canned verdict, and budgets are set
low so a synthetic stream breaches them. Covers:
  - the synthesis grader runs once in finalize, on the orchestrator step, against
    the objective + collected results, and its cost rolls up as validation_cost,
  - a max_subagents breach stops the run cleanly (status completed, partial
    synthesis preserved, budget_exceeded reported) rather than crashing.
"""
from __future__ import annotations

import asyncio

from backend.observability.event_adapter import (
    EventAdapter, BudgetExceeded, run_with_adapter)
from backend.schemas.models import EventType


class FakeDB:
    def __init__(self):
        self.steps, self.attempts, self.validations, self.run = {}, [], [], {}

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


class _Msg:
    def __init__(self, *, content="", name=None, tool_call_id=None, tool_calls=None,
                 tokens=(0, 0), tier=None):
        self.content, self.name, self.tool_call_id = content, name, tool_call_id
        self.tool_calls = tool_calls or []
        self.usage_metadata = {"input_tokens": tokens[0], "output_tokens": tokens[1]}
        h = {"x-vsr-selected-model": tier} if tier else {}
        self.response_metadata = {"headers": h, "model_name": tier}


class AIMessage(_Msg):
    pass


class ToolMessage(_Msg):
    pass


def _task(tcid, role):
    return AIMessage(tier="tier5", tokens=(100, 50),
                     tool_calls=[{"name": "task", "id": tcid,
                                  "args": {"subagent_type": role, "description": f"do {role}"}}])


def test_synthesis_grader_runs_on_orchestrator_step():
    fdb = FakeDB()
    seen = {}

    async def grader(objective, final_answer, results):
        seen["objective"] = objective
        seen["n_results"] = len(results)
        seen["answer"] = final_answer
        return {"level": "frontier", "verdict": "degraded", "score": 0.6,
                "critique": "dropped the cost comparison", "validator_tier": "tier4",
                "rubric_id": "synthesis_v1", "tokens_in": 500, "tokens_out": 40,
                "cost": 0.0012}

    adapter = EventAdapter("s1", persistence=fdb, synthesis_grader=grader, budget={})

    async def go():
        await adapter.start("compare A and B on cost and speed")
        # one delegation that closes with a usable result
        await adapter.handle((), "updates", {"model": {"messages": [_task("t1", "research")]}})
        await adapter.handle((), "updates", {"tools": {"messages": [ToolMessage(
            name="task", tool_call_id="t1",
            content='{"result":"A is 24x faster","confidence":0.8}')]}})
        # synthesis turn
        await adapter.handle((), "updates", {"model": {"messages": [AIMessage(
            tier="tier5", tokens=(200, 80), content="# Brief\nA beats B on speed.")]}})
        return await adapter.finalize()

    summary = asyncio.run(go())

    assert seen["objective"].startswith("compare A and B")
    assert seen["n_results"] == 1                       # collected the research result
    assert "A beats B" in seen["answer"]
    frontier = [v for v in fdb.validations if v["level"] == "frontier"]
    assert len(frontier) == 1
    assert frontier[0]["step_key"] == "orchestrator"    # graded on the synthesis step
    assert frontier[0]["verdict"] == "degraded"
    assert adapter.validation_cost >= 0.0012            # rolled up separately
    emitted = {e.event for e in adapter.events}
    assert EventType.validator_rejected in emitted      # degraded => flagged


def test_max_subagents_budget_stops_cleanly():
    fdb = FakeDB()
    adapter = EventAdapter("s2", persistence=fdb, budget={"max_subagents": 1})

    async def go():
        await adapter.start("q")
        # first delegation is fine
        await adapter.handle((), "updates", {"model": {"messages": [_task("t1", "research")]}})
        await adapter.handle((), "updates", {"tools": {"messages": [ToolMessage(
            name="task", tool_call_id="t1", content='{"result":"ok finding","confidence":0.8}')]}})
        # second delegation breaches max_subagents=1
        try:
            await adapter.handle((), "updates", {"model": {"messages": [_task("t2", "analysis")]}})
            raised = False
        except BudgetExceeded as be:
            raised = True
            adapter.budget_exceeded = {"kind": be.kind, "used": be.used, "limit": be.limit}
        return raised, await adapter.finalize(status="completed")

    raised, summary = asyncio.run(go())
    assert raised                                       # the breach was signalled
    assert summary["budget_exceeded"]["kind"] == "max_subagents"
    # run still finalizes cleanly with the partial result preserved
    assert fdb.run["status"] == "completed"
    assert fdb.steps["research-1"]["status"] == "completed"


def test_partial_synthesis_fills_empty_answer_on_breach():
    """When the graph is abandoned (budget stop) before the main agent synthesizes,
    finalize composes a final answer from the collected results via the partial
    synthesizer — the run delivers content instead of an empty answer."""
    fdb = FakeDB()
    calls = {}

    async def partial(objective, results):
        calls["objective"], calls["n"] = objective, len(results)
        return {"final_answer": "Partial: A beats B on speed.",
                "tokens_in": 300, "tokens_out": 60, "tier_observed": "T5", "cost": 0.001}

    adapter = EventAdapter("s4", persistence=fdb, partial_synthesizer=partial,
                           budget={"max_subagents": 1})

    async def go():
        await adapter.start("compare A and B")
        # one delegation completes, giving a usable result — but NO synthesis turn runs
        await adapter.handle((), "updates", {"model": {"messages": [_task("t1", "research")]}})
        await adapter.handle((), "updates", {"tools": {"messages": [ToolMessage(
            name="task", tool_call_id="t1", content='{"result":"A is fast","confidence":0.8}')]}})
        adapter.budget_exceeded = {"kind": "max_subagents", "used": 2, "limit": 1}
        return await adapter.finalize(status="completed")

    summary = asyncio.run(go())
    assert calls["n"] == 1                                  # composed from the one result
    assert adapter.final_answer == "Partial: A beats B on speed."
    assert summary["final_answer"] == "Partial: A beats B on speed."
    assert fdb.run["document_result"]["final_answer"].startswith("Partial:")
    assert adapter.total_tokens >= 360                      # partial-synth tokens rolled in


def test_partial_synthesis_skipped_when_answer_present():
    """The partial synthesizer must not overwrite a real synthesis the main agent produced."""
    fdb = FakeDB()

    async def partial(objective, results):  # should never be called
        raise AssertionError("partial synthesizer ran despite a real answer")

    adapter = EventAdapter("s5", persistence=fdb, partial_synthesizer=partial, budget={})

    async def go():
        await adapter.start("q")
        await adapter.handle((), "updates", {"model": {"messages": [_task("t1", "research")]}})
        await adapter.handle((), "updates", {"tools": {"messages": [ToolMessage(
            name="task", tool_call_id="t1", content='{"result":"finding","confidence":0.8}')]}})
        await adapter.handle((), "updates", {"model": {"messages": [AIMessage(
            tier="tier5", tokens=(200, 80), content="Real synthesis answer.")]}})
        return await adapter.finalize()

    summary = asyncio.run(go())
    assert summary["final_answer"] == "Real synthesis answer."


def test_run_with_adapter_catches_budget_and_completes():
    """End-to-end through run_with_adapter: a budget breach mid-stream stops the run,
    grades partial synthesis, and completes (not fails)."""
    fdb = FakeDB()

    class FakeAgent:
        async def astream(self, _input, _config, **_kw):
            yield (), "updates", {"model": {"messages": [_task("t1", "research")]}}
            yield (), "updates", {"tools": {"messages": [ToolMessage(
                name="task", tool_call_id="t1", content='{"result":"finding one","confidence":0.8}')]}}
            # breaches max_subagents=1
            yield (), "updates", {"model": {"messages": [_task("t2", "analysis")]}}
            # (graph would continue, but the adapter raises before consuming more)

    summary = asyncio.run(run_with_adapter(
        FakeAgent(), "q", "s3", persistence=fdb, budget={"max_subagents": 1}))

    assert summary["budget_exceeded"]["kind"] == "max_subagents"
    assert fdb.run["status"] == "completed"             # clean stop, not failed
    emitted_run_completed = fdb.run.get("metrics", {}).get("budget_exceeded")
    assert emitted_run_completed and emitted_run_completed["kind"] == "max_subagents"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
