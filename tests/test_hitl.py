"""
Offline tests for Stage 5 HITL plan approval.

The live interrupt requires a real planning run on the gateway; here a fake agent
emits a langgraph-style `__interrupt__` chunk on its first astream and the rest of
the run on resume, so the approve/reject control flow in run_with_adapter is
exercised deterministically. Also covers the env-driven interrupt config.
"""
from __future__ import annotations

import asyncio

from backend.observability.event_adapter import run_with_adapter
from backend.schemas.models import EventType
from backend.agents import core


class FakeDB:
    def __init__(self):
        self.steps, self.attempts, self.validations, self.run = {}, [], [], {}
        self.status_calls = []

    async def create_step(self, run_id, *, step_key, type, **kw):
        self.steps.setdefault(step_key, {"type": type, "status": "running", **kw})

    async def set_step_status(self, run_id, step_key, status, **kw):
        self.steps.setdefault(step_key, {}).update(status=status, **kw)

    async def set_run_status(self, run_id, status, **kw):
        self.status_calls.append(status)

    async def record_attempt(self, run_id, step_key, **kw):
        self.attempts.append({"step_key": step_key, **kw})

    async def record_validation(self, run_id, step_key, *, level, verdict, **kw):
        self.validations.append({"step_key": step_key, "level": level, "verdict": verdict, **kw})

    async def finalize_run(self, run_id, **kw):
        self.run = kw


class _Msg:
    def __init__(self, *, content="", name=None, tool_call_id=None, tool_calls=None,
                 tokens=(0, 0), tier=None):
        self.content, self.name, self.tool_call_id = content, name, tool_call_id
        self.tool_calls = tool_calls or []
        self.usage_metadata = {"input_tokens": tokens[0], "output_tokens": tokens[1]}
        self.response_metadata = {"headers": {"x-vsr-selected-model": tier} if tier else {}}


class AIMessage(_Msg):
    pass


class InterruptingAgent:
    """astream yields an interrupt on the initial input; on a Command(resume=...)
    input it streams the synthesis turn. Branching on input type avoids any loop."""
    async def astream(self, stream_input, config, **kw):
        from langgraph.types import Command
        if isinstance(stream_input, Command):
            yield (), "updates", {"model": {"messages": [AIMessage(
                tier="tier5", tokens=(100, 40), content="# Final answer after approval")]}}
        else:
            # plan produced → pause for approval
            yield (), "updates", {"__interrupt__": ("approve plan?",)}


def test_build_interrupts_env(monkeypatch):
    monkeypatch.setenv("ADL_PLAN_APPROVAL", "1")
    monkeypatch.setenv("ADL_SENSITIVE_TOOLS", "code_exec, ticket_create")
    monkeypatch.setenv("ADL_PLAN_TOOL", "write_todos")
    ints = core.build_interrupts()
    assert "write_todos" in ints
    assert ints["code_exec"]["allowed_decisions"] == ["approve", "reject"]
    assert "ticket_create" in ints


def test_build_interrupts_default_empty(monkeypatch):
    monkeypatch.delenv("ADL_PLAN_APPROVAL", raising=False)
    monkeypatch.delenv("ADL_SENSITIVE_TOOLS", raising=False)
    assert core.build_interrupts() == {}


def test_hitl_approve_resumes_and_completes():
    fdb = FakeDB()
    agent = InterruptingAgent()

    async def approval():
        return "approve"

    summary = asyncio.run(run_with_adapter(
        agent, "do the thing", "h1", persistence=fdb, approval=approval))

    assert "awaiting_approval" in fdb.status_calls          # paused
    assert "running" in fdb.status_calls                    # resumed
    assert summary["status"] == "completed"
    assert "after approval" in (summary["final_answer"] or "")
    emitted = {e.event for e in []}  # events live on the adapter; check via DB status instead
    assert fdb.run["status"] == "completed"


def test_hitl_reject_aborts():
    fdb = FakeDB()
    agent = InterruptingAgent()

    async def approval():
        return "reject"

    summary = asyncio.run(run_with_adapter(
        agent, "do the thing", "h2", persistence=fdb, approval=approval))

    assert "awaiting_approval" in fdb.status_calls
    assert summary["status"] == "aborted"
    assert fdb.run["status"] == "aborted"


if __name__ == "__main__":
    print("run via pytest (uses monkeypatch fixture)")
