"""
Offline unit tests for L1/L2 judge validation + bounded retry in the event adapter.

No gateway: a fake judge returns canned verdicts and a fake re-dispatch returns
canned worker output, so the retry loop's control flow is deterministic. Covers:
  - judge runs only for judge/frontier roles (mechanical roles skip it),
  - reject → re-dispatch → pass within the retry cap,
  - reject every time → exhaust the cap → degraded (never silently passed),
  - judge token spend rolls up separately as validation_tokens.
"""
from __future__ import annotations

import asyncio

from backend.observability.event_adapter import EventAdapter
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


def _delegate_and_close(tcid, role, result):
    """A minimal stream: planner delegates `role`, the delegation closes with `result`."""
    return [
        ((), "updates", {"model": {"messages": [AIMessage(
            tier="tier5", tokens=(100, 50),
            tool_calls=[{"name": "task", "id": tcid,
                         "args": {"subagent_type": role, "description": f"do {role}"}}])]}}),
        ((), "updates", {"tools": {"messages": [ToolMessage(
            name="task", tool_call_id=tcid, content=result)]}}),
    ]


def _run(adapter, stream):
    async def go():
        await adapter.start("q")
        for ns, mode, chunk in stream:
            await adapter.handle(ns, mode, chunk)
        return await adapter.finalize()
    return asyncio.run(go())


def test_judge_passes_clean_result():
    fdb = FakeDB()
    calls = []

    async def judge(subtask, result_text, role, cfg):
        calls.append(role)
        return {"level": "judge", "verdict": "pass", "score": 0.95, "critique": "",
                "validator_tier": "tier1", "rubric_id": "research_v1",
                "tokens_in": 80, "tokens_out": 12}

    adapter = EventAdapter("r1", persistence=fdb, judge=judge, redispatch=None,
                           validation_cfg={"research": {"level": "judge", "tier": "tier1",
                                                        "rubric": "research_v1", "retries": 1}})
    _run(adapter, _delegate_and_close("t1", "research", '{"result":"a real finding with numbers 24x","confidence":0.8}'))

    assert calls == ["research"]                                   # judge ran
    judged = [v for v in fdb.validations if v["level"] == "judge"]
    assert len(judged) == 1 and judged[0]["verdict"] == "pass"
    assert fdb.steps["research-1"]["status"] == "completed"
    emitted = {e.event for e in adapter.events}
    assert EventType.validator_approved in emitted
    assert adapter.validation_tokens == 92                         # rolled up separately


def test_mechanical_role_skips_judge():
    fdb = FakeDB()

    async def judge(*a, **k):
        raise AssertionError("judge must not run for a mechanical role")

    adapter = EventAdapter("r2", persistence=fdb, judge=judge,
                           validation_cfg={"fact_check": {"level": "mechanical", "retries": 0}})
    _run(adapter, _delegate_and_close("t1", "fact_check",
                                      '{"result":"2 supported, 1 unsupported","confidence":0.7}'))
    assert all(v["level"] == "mechanical" for v in fdb.validations)
    assert fdb.steps["fact_check-1"]["status"] == "completed"


def test_reject_then_redispatch_then_pass():
    fdb = FakeDB()
    verdicts = iter(["fail", "pass"])

    async def judge(subtask, result_text, role, cfg):
        v = next(verdicts)
        return {"level": "judge", "verdict": v, "score": 0.4 if v == "fail" else 0.9,
                "critique": "missing numbers" if v == "fail" else "",
                "validator_tier": "tier1", "rubric_id": "analysis_v1",
                "tokens_in": 50, "tokens_out": 10}

    async def redispatch(role, subtask, critique):
        return {"result": '{"result":"now with 24x speedup numbers","confidence":0.9}',
                "tokens_in": 200, "tokens_out": 120, "tier_observed": "T2", "cache_hit": False}

    adapter = EventAdapter("r3", persistence=fdb, judge=judge, redispatch=redispatch,
                           validation_cfg={"analysis": {"level": "judge", "tier": "tier2",
                                                        "rubric": "analysis_v1", "retries": 1}})
    _run(adapter, _delegate_and_close("t1", "analysis", '{"result":"vague","confidence":0.5}'))

    assert fdb.steps["analysis-1"]["status"] == "completed"        # recovered
    # one re-dispatch happened → a worker attempt was recorded for it
    redispatched = [a for a in fdb.attempts if a["tier_observed"] == "T2"]
    assert len(redispatched) == 1
    emitted = {e.event for e in adapter.events}
    assert EventType.worker_retrying in emitted
    assert EventType.validator_approved in emitted                 # final verdict pass


def test_reject_exhausts_cap_then_degraded():
    """Judge never satisfied, but the worker keeps producing usable (non-empty) JSON.
    On retry-cap exhaustion the step is DEGRADED and surfaced, not silently passed
    and not hard-failed (the result still feeds synthesis)."""
    fdb = FakeDB()

    async def judge(subtask, result_text, role, cfg):
        return {"level": "judge", "verdict": "fail", "score": 0.2, "critique": "still wrong",
                "validator_tier": "tier1", "rubric_id": "analysis_v1",
                "tokens_in": 50, "tokens_out": 10}

    redispatch_n = {"n": 0}

    async def redispatch(role, subtask, critique):
        redispatch_n["n"] += 1
        return {"result": '{"result":"still vague but present","confidence":0.5}',
                "tokens_in": 100, "tokens_out": 60, "tier_observed": "T2", "cache_hit": False}

    adapter = EventAdapter("r4", persistence=fdb, judge=judge, redispatch=redispatch,
                           validation_cfg={"analysis": {"level": "judge", "tier": "tier2",
                                                        "rubric": "analysis_v1", "retries": 2}})
    _run(adapter, _delegate_and_close("t1", "analysis", '{"result":"vague","confidence":0.5}'))

    assert redispatch_n["n"] == 2                                  # bounded by retries=2
    # usable result → completed-but-degraded, surfaced (never silently passed)
    assert fdb.steps["analysis-1"]["status"] == "completed"
    emitted = {e.event for e in adapter.events}
    assert EventType.validator_rejected in emitted                # flagged below-bar
    assert EventType.worker_rejected_final in emitted             # cap exhausted
    assert EventType.task_completed in emitted                    # result still passed on


def test_empty_result_hard_fails():
    """A genuinely empty result is L0-fail → step hard-fails (nothing usable)."""
    fdb = FakeDB()
    adapter = EventAdapter("r5", persistence=fdb,
                           validation_cfg={"research": {"level": "judge", "retries": 0}})
    _run(adapter, _delegate_and_close("t1", "research", "   "))
    assert fdb.steps["research-1"]["status"] == "failed"
    emitted = {e.event for e in adapter.events}
    assert EventType.task_failed in emitted


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
