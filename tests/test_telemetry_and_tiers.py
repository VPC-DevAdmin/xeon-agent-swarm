"""Unit tests for spec v3 foundation: tier helpers, verifier weighting,
per-step verdict alias, and router telemetry-header parsing."""
import pytest

from backend.schemas.models import (
    TIER_ORDER,
    CallTelemetry,
    StepEvalVerdict,
    TaskGraph,
    TaskSpec,
    TaskType,
    VerifierScores,
    bump_tier,
    is_top_tier,
    weighted_total,
)
from backend.inference.client import _parse_route_decision, _telemetry_from


def test_bump_tier_saturates():
    assert bump_tier("L1") == "L2"
    assert bump_tier("L4") == "L5"
    assert bump_tier("L5") == "L5"        # saturates at the top
    assert bump_tier(None) == "L2"        # unknown -> one above the floor
    assert bump_tier("bogus") == "L2"


def test_is_top_tier():
    assert is_top_tier("L5") is True
    assert is_top_tier("L4") is False
    assert is_top_tier(None) is False


def test_weighted_total():
    # All 1.0 -> weights sum to 1.0
    assert weighted_total(VerifierScores(
        coverage=1, decomposition_soundness=1, dependency_correctness=1,
        tier_appropriateness=1, verifiability=1)) == 1.0
    # Coverage only (weight 0.30)
    assert weighted_total(VerifierScores(coverage=1.0)) == 0.30


def test_step_eval_pass_alias():
    # The router emits `"pass": true`; the field is `passed` with alias "pass".
    v = StepEvalVerdict.model_validate({"subtask_id": "s3", "pass": True, "score": 0.9})
    assert v.passed is True
    assert v.score == 0.9
    # Schema exposes the wire name "pass", not "passed".
    props = StepEvalVerdict.model_json_schema()["properties"]
    assert "pass" in props and "passed" not in props


def test_task_spec_defaults_have_tier_hint_and_contract():
    t = TaskSpec(type=TaskType.research)
    assert t.tier_hint == "L2"
    assert t.output_contract == ""
    g = TaskGraph(query="q", tasks=[t], reasoning="r")
    assert g.plan_id and g.strategy_note == ""


def test_parse_route_decision_rich_and_minimal():
    rich = _parse_route_decision("worker@qwen3-30b?classified=L2&min=L3&served=L3")
    assert rich == {"classified": "L2", "served": "L3", "min": "L3"}
    minimal = _parse_route_decision("worker@qwen3-4b?tier=L2")
    assert minimal["served"] == "L2" and minimal["classified"] is None
    assert _parse_route_decision("planner@qwen3-30b") == {
        "classified": None, "served": None, "min": None}


class _Usage:
    prompt_tokens = 120
    completion_tokens = 40


class _Parsed:
    usage = _Usage()


def test_telemetry_from_headers():
    headers = {
        "x-llm-model-served": "Qwen/Qwen3-30B-A3B-FP8",
        "x-llm-route-decision": "worker@qwen3-30b?classified=L2&min=L3&served=L3",
        "x-llm-cost-usd": "0.0012",
    }
    tel = _telemetry_from(headers, _Parsed(), latency_ms=210.5, truncated=True)
    assert isinstance(tel, CallTelemetry)
    assert tel.model_served == "Qwen/Qwen3-30B-A3B-FP8"
    assert tel.cost_usd == 0.0012
    assert (tel.classified_tier, tel.served_tier, tel.min_tier) == ("L2", "L3", "L3")
    assert tel.tokens_in == 120 and tel.tokens_out == 40
    assert tel.truncated is True


def test_telemetry_tolerates_missing_headers():
    tel = _telemetry_from({}, _Parsed(), latency_ms=10.0, truncated=False)
    assert tel.cost_usd == 0.0 and tel.model_served == "" and tel.served_tier is None
