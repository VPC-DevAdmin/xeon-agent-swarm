"""Best-of-N decompose-and-verify selection logic (spec v3 §4-5).

Mocks the planner/verifier model calls and exercises plan_phase's selection,
fatal-flaw exclusion, and replan-on-failure paths — no network."""
import pytest

from backend.agents import planner
from backend.schemas.models import (
    TaskGraph,
    TaskSpec,
    TaskType,
    VerifierScores,
    VerifierVerdict,
)


def _plan(pid: str) -> TaskGraph:
    # Single task = the synthesis sink, so it passes the mechanical gate that
    # plan_phase now runs before verification (spec v6 §5).
    return TaskGraph(
        query="q", reasoning="r", plan_id=pid,
        tasks=[TaskSpec(id="s1", type=TaskType.writing, is_synthesis=True,
                        success_criteria=["produces a correct answer"])],
    )


@pytest.mark.asyncio
async def test_plan_phase_selects_highest_weighted(monkeypatch):
    # Three candidates with distinct verifier scores; the best non-fatal wins.
    plans = {"a": _plan("a"), "b": _plan("b"), "c": _plan("c")}
    order = iter(["a", "b", "c"])

    async def fake_candidate(query, run_id, seed, *, critique=None):
        return plans[next(order)]

    scores = {
        "a": VerifierScores(coverage=0.5, decomposition_soundness=0.5,
                            dependency_correctness=0.5, tier_appropriateness=0.5,
                            verifiability=0.5),
        "b": VerifierScores(coverage=0.9, decomposition_soundness=0.9,
                            dependency_correctness=0.9, tier_appropriateness=0.9,
                            verifiability=0.9),   # winner
        "c": VerifierScores(coverage=0.7, decomposition_soundness=0.7,
                            dependency_correctness=0.7, tier_appropriateness=0.7,
                            verifiability=0.7),
    }

    async def fake_verify(query, candidate, run_id):
        return VerifierVerdict(plan_id=candidate.plan_id, scores=scores[candidate.plan_id])

    monkeypatch.setattr(planner, "plan_candidate", fake_candidate)
    monkeypatch.setattr(planner, "verify_plan", fake_verify)

    chosen = await planner.plan_phase("q", "run1", n=3, tau=0.6)
    assert chosen.plan_id == "b"


@pytest.mark.asyncio
async def test_plan_phase_excludes_fatal_and_replans(monkeypatch):
    # Round 1: all candidates carry a fatal flaw -> replan. Round 2: clean winner.
    rounds = {"n": 0}

    async def fake_candidate(query, run_id, seed, *, critique=None):
        return _plan(f"r{rounds['n']}")

    async def fake_verify(query, candidate, run_id):
        if rounds["n"] == 0:
            return VerifierVerdict(plan_id=candidate.plan_id,
                                   fatal_flaws=["dependency cycle"])
        return VerifierVerdict(
            plan_id=candidate.plan_id,
            scores=VerifierScores(coverage=1, decomposition_soundness=1,
                                  dependency_correctness=1, tier_appropriateness=1,
                                  verifiability=1))

    async def counting_candidate(*a, **k):
        return await fake_candidate(*a, **k)

    # bump the round after the first batch of verifies resolves
    orig_verify = fake_verify
    seen = {"c": 0}

    async def verify_then_advance(query, candidate, run_id):
        v = await orig_verify(query, candidate, run_id)
        seen["c"] += 1
        if seen["c"] % 3 == 0:   # after a full round of 3
            rounds["n"] += 1
        return v

    monkeypatch.setattr(planner, "plan_candidate", counting_candidate)
    monkeypatch.setattr(planner, "verify_plan", verify_then_advance)

    chosen = await planner.plan_phase("q", "run2", n=3, tau=0.6, replan_budget=1)
    assert chosen.plan_id.startswith("r1")   # the clean second-round plan


@pytest.mark.asyncio
async def test_plan_phase_raises_when_all_fatal_and_no_budget(monkeypatch):
    async def fake_candidate(query, run_id, seed, *, critique=None):
        return _plan("x")

    async def fake_verify(query, candidate, run_id):
        return VerifierVerdict(plan_id="x", fatal_flaws=["unreachable subtask"])

    monkeypatch.setattr(planner, "plan_candidate", fake_candidate)
    monkeypatch.setattr(planner, "verify_plan", fake_verify)

    with pytest.raises(planner.PlanningFailed):
        await planner.plan_phase("q", "run3", n=2, tau=0.6, replan_budget=0)


@pytest.mark.asyncio
async def test_plan_phase_broadcasts_selection(monkeypatch):
    async def fake_candidate(query, run_id, seed, *, critique=None):
        return _plan("only")

    async def fake_verify(query, candidate, run_id):
        return VerifierVerdict(
            plan_id="only", rationale="clean",
            scores=VerifierScores(coverage=1, decomposition_soundness=1,
                                  dependency_correctness=1, tier_appropriateness=1,
                                  verifiability=1))

    monkeypatch.setattr(planner, "plan_candidate", fake_candidate)
    monkeypatch.setattr(planner, "verify_plan", fake_verify)

    events = []

    async def broadcast(run_id, ev):
        events.append(ev)

    await planner.plan_phase("q", "run4", broadcast=broadcast, n=1, tau=0.6)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["_selection"]["score"] == 1.0
    assert payload["_selection"]["n_candidates"] == 1
