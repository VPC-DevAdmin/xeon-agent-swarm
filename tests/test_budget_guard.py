"""Graceful budget enforcement: budgets decline excess work, visibly.

The guards run inside the agent graph. A plan that over-delegates is rejected
whole before any worker spawns and the planner replans with feedback, bounded.
A worker's tool calls past the hop budget return a finalize-now notice instead
of executing. These tests drive the middlewares directly with synthetic
transcripts.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from langchain_core.messages import AIMessage, ToolMessage

from backend.agents.budget_guard import (MAX_REPLANS, PLAN_REJECT_PREFIX,
                                          TOOL_EXHAUSTED_PREFIX,
                                          PlanBudgetGuard, ToolBudgetGuard)
from backend.observability.event_adapter import BudgetExceeded


def _plan_msg(n: int, start: int = 0) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "task", "id": f"t{start + i}", "args": {"subagent_type": "research"}}
        for i in range(n)])


def _req(messages, tool_call):
    return SimpleNamespace(state={"messages": messages}, tool_call=tool_call,
                           tool=None, runtime=None)


def _executed(tcid: str) -> ToolMessage:
    return ToolMessage(name="task", tool_call_id=tcid, content="done")


def _rejected(tcid: str) -> ToolMessage:
    return ToolMessage(name="task", tool_call_id=tcid,
                       content=f"{PLAN_REJECT_PREFIX} replan")


RAN = object()


def _handler(request):
    return RAN


# ── the plan guard ───────────────────────────────────────────────────────────

def test_a_within_budget_plan_runs():
    guard = PlanBudgetGuard(max_subagents=5)
    msg = _plan_msg(3)
    for tc in msg.tool_calls:
        assert guard.wrap_tool_call(_req([msg], tc), _handler) is RAN


def test_an_oversized_plan_is_rejected_whole_before_any_worker_spawns():
    """Every call in the batch is declined, not just the excess: a clamped
    5-worker unit would be a silently bigger unit than the declared plan."""
    guard = PlanBudgetGuard(max_subagents=5)
    msg = _plan_msg(6)
    for tc in msg.tool_calls:
        out = guard.wrap_tool_call(_req([msg], tc), _handler)
        assert out is not RAN
        assert out.content.startswith(PLAN_REJECT_PREFIX)
        assert "Replan" in out.content and "at most 5" in out.content


def test_executed_delegations_count_against_later_batches():
    """Three workers already ran; a second wave of three busts a budget of 5
    and is rejected with the remaining allowance named."""
    guard = PlanBudgetGuard(max_subagents=5)
    first = _plan_msg(3)
    history = [first] + [_executed(f"t{i}") for i in range(3)]
    second = _plan_msg(3, start=3)
    out = guard.wrap_tool_call(_req(history + [second], second.tool_calls[0]),
                               _handler)
    assert out.content.startswith(PLAN_REJECT_PREFIX)
    assert "at most 2" in out.content


def test_rejected_plans_do_not_count_as_executed_work():
    guard = PlanBudgetGuard(max_subagents=5)
    oversized = _plan_msg(6)
    history = [oversized] + [_rejected(f"t{i}") for i in range(6)]
    retry = _plan_msg(3, start=10)
    for tc in retry.tool_calls:
        assert guard.wrap_tool_call(_req(history + [retry], tc), _handler) is RAN


def test_the_replan_loop_is_bounded():
    """After MAX_REPLANS rejected plans, the next oversized plan raises and
    the unit fails, which by then it deserves to."""
    guard = PlanBudgetGuard(max_subagents=5)
    history = []
    for round_n in range(MAX_REPLANS):
        msg = _plan_msg(6, start=round_n * 10)
        out = guard.wrap_tool_call(_req(history + [msg], msg.tool_calls[0]),
                                   _handler)
        assert out.content.startswith(PLAN_REJECT_PREFIX)
        history += [msg] + [_rejected(tc["id"]) for tc in msg.tool_calls]
    final = _plan_msg(6, start=99)
    with pytest.raises(BudgetExceeded):
        guard.wrap_tool_call(_req(history + [final], final.tool_calls[0]),
                             _handler)


def test_non_task_tools_pass_through_the_plan_guard():
    guard = PlanBudgetGuard(max_subagents=1)
    tc = {"name": "bench_record", "id": "x1", "args": {}}
    assert guard.wrap_tool_call(_req([], tc), _handler) is RAN


# ── the tool guard ───────────────────────────────────────────────────────────

def test_tools_run_under_the_hop_budget():
    guard = ToolBudgetGuard(max_tool_hops=3)
    history = [ToolMessage(name="bench_record", tool_call_id=f"h{i}", content="ok")
               for i in range(2)]
    tc = {"name": "bench_record", "id": "h9", "args": {}}
    assert guard.wrap_tool_call(_req(history, tc), _handler) is RAN


def test_the_hop_past_the_budget_declines_instead_of_running():
    guard = ToolBudgetGuard(max_tool_hops=3)
    history = [ToolMessage(name="bench_record", tool_call_id=f"h{i}", content="ok")
               for i in range(3)]
    tc = {"name": "bench_record", "id": "h9", "args": {}}
    out = guard.wrap_tool_call(_req(history, tc), _handler)
    assert out is not RAN
    assert out.content.startswith(TOOL_EXHAUSTED_PREFIX)
    assert "Finalize" in out.content
