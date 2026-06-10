"""min_tier escalation loop (decompose-verify spec v3 §6-7).

Mocks the worker call + per-step evaluator and verifies: a failed eval bumps
min_tier one tier above the served tier, a pass stops immediately, and an L5
failure short-circuits (saturation)."""
import pytest

from backend.agents import worker
from backend.schemas.models import (
    AgentResult,
    StepEvalVerdict,
    TaskSpec,
    TaskStatus,
    TaskType,
)


def _result(served: str | None) -> AgentResult:
    return AgentResult(task_id="s1", status=TaskStatus.completed, result="out",
                       confidence=0.8, model_used="m", hardware="cpu",
                       latency_ms=1.0, served_tier=served)


async def _noop(run_id, ev):
    pass


@pytest.mark.asyncio
async def test_escalation_bumps_min_tier_above_served(monkeypatch):
    task = TaskSpec(id="s1", type=TaskType.research, output_contract="c")
    calls = []          # records the min_tier passed to each execute_task
    served_seq = iter(["L2", "L3"])   # router serves L2 first, then L3 (floored)

    async def fake_execute(t, run_id, broadcast, context=None, min_tier=None):
        calls.append(min_tier)
        return _result(next(served_seq))

    # First eval fails, second passes.
    evals = iter([StepEvalVerdict(passed=False, score=0.3, fix_hint="more detail"),
                  StepEvalVerdict(passed=True, score=0.9)])

    async def fake_eval(t, result, run_id):
        return next(evals)

    monkeypatch.setattr(worker, "execute_task", fake_execute)
    monkeypatch.setattr("backend.agents.evaluator.evaluate_step", fake_eval)

    out = await worker.execute_task_with_escalation(task, "run", _noop, retry_budget=1)
    # First call had no floor; the retry was floored to L3 (one above served L2).
    assert calls == [None, "L3"]
    assert out.served_tier == "L3"


@pytest.mark.asyncio
async def test_escalation_stops_on_pass(monkeypatch):
    task = TaskSpec(id="s1", type=TaskType.research, output_contract="c")
    n = {"execs": 0}

    async def fake_execute(t, run_id, broadcast, context=None, min_tier=None):
        n["execs"] += 1
        return _result("L2")

    async def fake_eval(t, result, run_id):
        return StepEvalVerdict(passed=True, score=0.95)

    monkeypatch.setattr(worker, "execute_task", fake_execute)
    monkeypatch.setattr("backend.agents.evaluator.evaluate_step", fake_eval)

    await worker.execute_task_with_escalation(task, "run", _noop, retry_budget=2)
    assert n["execs"] == 1          # passed first time → no retries


@pytest.mark.asyncio
async def test_escalation_saturates_at_top_tier(monkeypatch):
    task = TaskSpec(id="s1", type=TaskType.research, output_contract="c")
    n = {"execs": 0}

    async def fake_execute(t, run_id, broadcast, context=None, min_tier=None):
        n["execs"] += 1
        return _result("L5")        # already top tier

    async def fake_eval(t, result, run_id):
        return StepEvalVerdict(passed=False, score=0.1)

    monkeypatch.setattr(worker, "execute_task", fake_execute)
    monkeypatch.setattr("backend.agents.evaluator.evaluate_step", fake_eval)

    out = await worker.execute_task_with_escalation(task, "run", _noop, retry_budget=2)
    # L5 failure short-circuits — no wasted retry at the top tier.
    assert n["execs"] == 1
    assert out.served_tier == "L5"
