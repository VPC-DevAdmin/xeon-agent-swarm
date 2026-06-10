"""Best-of-N planner with generative rubric verification (decompose-verify spec v3 §4-5).

Generates N candidate TaskGraphs from strategy-seeded planner calls, scores each
with the verifier rubric, drops fatal-flawed candidates, and selects the highest
weighted_total. Replans with the best repair_hint when nothing clears the bar.

The planner role is pinned to the strongest tier and sampled diversely; diversity
comes from strategy seeding, not from cranking temperature (Qwen3 degrades in
thinking mode at high temp). The verifier is pinned + low-temp for a consistent
judge. Both are router roles (model="planner" / "verifier").
"""
from __future__ import annotations

import asyncio
import logging
import os

from backend.agents.orchestrator import ORCHESTRATOR_SYSTEM
from backend.inference.client import InferenceClient, llm_endpoint, llm_model_for
from backend.observability.trace import trace_headers
from backend.schemas.models import (
    EventType,
    SwarmEvent,
    TaskGraph,
    VerifierVerdict,
    weighted_total,
)

logger = logging.getLogger(__name__)


class PlanningFailed(Exception):
    """No candidate plan cleared the verifier without fatal flaws."""


# Strategy seeds force structurally different candidates the verifier can discriminate.
STRATEGY_SEEDS = [
    "favor maximum parallelism across independent subtasks",
    "favor a conservative sequential chain that de-risks dependencies",
    "favor minimal subtask count; merge where one call can do more",
]

PLANNER_GUIDANCE = """
ADDITIONAL PLANNER RULES (decompose-verify):
- Set `tier_hint` per task: an advisory difficulty estimate L1..L5 (L1 cheapest,
  L5 strongest). L1 lookup/format/extract; L2 single-hop reasoning or
  summarization; L3 multi-step reasoning over retrieved context; L4 cross-source
  synthesis or ambiguous requirements; L5 novel or high-stakes reasoning.
- Set `output_contract` per task: one sentence stating what a correct, complete
  output for that task contains. The evaluator scores against it.
- Set `strategy_note`: one line describing the decomposition approach you took.
- Apply THIS decomposition strategy this round: {seed}.
""".strip()

VERIFIER_SYSTEM = """
You are a plan verifier. You are given a goal and a candidate task-graph
decomposition. Score the plan on five dimensions, each from 0.0 to 1.0:

- coverage (0.30): do the subtasks together satisfy the goal with no gaps?
- decomposition_soundness (0.25): right granularity — genuinely separable, not
  too coarse, not atomized?
- dependency_correctness (0.25): are depends_on edges complete, acyclic, with no
  false dependencies?
- tier_appropriateness (0.10): are the tier_hints reasonable for each subtask?
- verifiability (0.10): does every subtask have a concrete output_contract?

Record FATAL FLAWS (hard-exclude) in `fatal_flaws`: a dependency cycle; a subtask
unreachable from the goal; a synthesis/task referencing a missing subtask id; a
goal requirement covered by no subtask.

Give a two-or-three sentence `rationale` on the deciding factors, and a single
`repair_hint`: the most useful change if the score is low. Do NOT compute a total;
the caller weights the scores. Respond with ONLY the VerifierVerdict JSON.
""".strip()


def _planner_client() -> InferenceClient:
    return InferenceClient(base_url=llm_endpoint(), model=llm_model_for("planner"),
                           hardware="cpu", use_semaphore=False)


def _verifier_client() -> InferenceClient:
    return InferenceClient(base_url=llm_endpoint(), model=llm_model_for("verifier"),
                           hardware="cpu", use_semaphore=False)


async def plan_candidate(query: str, run_id: str, seed: str,
                         *, critique: str | None = None) -> TaskGraph:
    """One strategy-seeded planner call → one candidate TaskGraph."""
    client = _planner_client()
    system = ORCHESTRATOR_SYSTEM + "\n\n" + PLANNER_GUIDANCE.format(seed=seed)
    user = query
    if critique:
        user += f"\n\n[REPLAN] A prior decomposition scored low. Fix this: {critique}"
    tg = await client.complete_structured(
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_model=TaskGraph, max_tokens=4096,
        extra_headers=trace_headers(run_id, "plan"),
        metadata={"run_id": run_id, "step_key": "plan"},
    )
    if not tg.strategy_note:
        tg.strategy_note = seed
    return tg


async def verify_plan(query: str, candidate: TaskGraph, run_id: str) -> VerifierVerdict:
    """Score one candidate plan against the rubric."""
    client = _verifier_client()
    user = f"GOAL:\n{query}\n\nCANDIDATE PLAN:\n{candidate.model_dump_json(indent=2)}"
    verdict = await client.complete_structured(
        messages=[{"role": "system", "content": VERIFIER_SYSTEM},
                  {"role": "user", "content": user}],
        response_model=VerifierVerdict, max_tokens=1024,
        extra_headers=trace_headers(run_id, "verify"),
        metadata={"run_id": run_id, "step_key": "verify"},
    )
    verdict.plan_id = candidate.plan_id
    return verdict


async def plan_phase(query: str, run_id: str, broadcast=None, *,
                     n: int = 3, replan_budget: int = 1,
                     tau: float | None = None, feedback: str | None = None) -> TaskGraph:
    """Best-of-N decompose-and-verify. Returns the selected plan; broadcasts
    graph_ready for the winner. Raises PlanningFailed if nothing survives."""
    if tau is None:
        tau = float(os.getenv("PLAN_TAU", "0.6"))
    seeds = (STRATEGY_SEEDS * (n // len(STRATEGY_SEEDS) + 1))[:n]

    candidates = await asyncio.gather(
        *[plan_candidate(query, run_id, s, critique=feedback) for s in seeds])
    verdicts = await asyncio.gather(
        *[verify_plan(query, c, run_id) for c in candidates])

    scored = [(c, weighted_total(v.scores), v)
              for c, v in zip(candidates, verdicts) if not v.fatal_flaws]

    if not scored:
        if replan_budget > 0:
            hint = next((v.repair_hint for v in verdicts if v.repair_hint),
                        "every candidate had a fatal flaw")
            logger.info("[plan_phase] all candidates fatally flawed — replanning")
            return await plan_phase(query, run_id, broadcast, n=n,
                                    replan_budget=replan_budget - 1, tau=tau, feedback=hint)
        raise PlanningFailed("no candidate plan without fatal flaws")

    best_plan, best_score, best_verdict = max(scored, key=lambda x: x[1])

    if best_score < tau and replan_budget > 0:
        logger.info("[plan_phase] best score %.3f < tau %.3f — replanning", best_score, tau)
        return await plan_phase(query, run_id, broadcast, n=n,
                                replan_budget=replan_budget - 1, tau=tau,
                                feedback=best_verdict.repair_hint)

    if broadcast:
        await broadcast(run_id, SwarmEvent(
            event=EventType.graph_ready, run_id=run_id,
            payload={**best_plan.model_dump(),
                     "_selection": {"score": best_score,
                                    "n_candidates": len(candidates),
                                    "rationale": best_verdict.rationale}}))
    return best_plan
