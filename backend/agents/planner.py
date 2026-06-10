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


# Strategy seeds force structurally different candidates the verifier can
# discriminate. A run takes the first N_CANDIDATES of this pool (spec v6 §4).
# Override the pool with PLAN_STRATEGIES (one strategy per line, or '||'-separated);
# add strategies rather than raising N to push accuracy past sampling noise.
_DEFAULT_STRATEGIES = [
    "favor maximum parallelism across independent subtasks",
    "favor a conservative sequential chain that de-risks dependencies",
    "favor minimal subtask count; merge where one call can do more",
]


def _plan_strategies() -> list[str]:
    raw = os.getenv("PLAN_STRATEGIES", "").strip()
    if not raw:
        return _DEFAULT_STRATEGIES
    parts = raw.split("||") if "||" in raw else raw.splitlines()
    pool = [p.strip() for p in parts if p.strip()]
    return pool or _DEFAULT_STRATEGIES


def _n_candidates(override: int | None) -> int:
    if override is not None:
        return max(1, override)
    return max(1, int(os.getenv("N_CANDIDATES", "3")))


PLANNER_GUIDANCE = """
ADDITIONAL PLANNER RULES (decompose-verify):
- Set `tier_hint` per task: an advisory difficulty estimate L1..L5 (L1 cheapest,
  L5 strongest). L1 lookup/format/extract; L2 single-hop reasoning or
  summarization; L3 multi-step reasoning over retrieved context; L4 cross-source
  synthesis or ambiguous requirements; L5 novel or high-stakes reasoning.
- Set `success_criteria` per task: the checklist of conditions a correct, complete
  output must satisfy. This is the single definition of done; the evaluator scores
  the worker output against it. `objective` is the human-readable one-liner.
- Mark exactly ONE terminal task with `is_synthesis: true` — the node that combines
  the others into the final answer. It must `depends_on` every task whose output it
  needs, and every other task must feed it (directly or transitively).
- Set `retrieval` per task: {{"needed": bool, "query": "...", "top_n": int}}. Set
  needed=true ONLY when the task requires external/grounded context; then give a
  FOCUSED search query (a few keywords or a precise question — NOT the whole
  objective) and a top_n of 3-8. For pure-reasoning, synthesis, or code tasks set
  needed=false so no retrieval is wasted.
- Set `strategy_note`: one line describing the decomposition approach you took.
- Apply THIS decomposition strategy this round: {seed}.
""".strip()

# De-mechanized verifier prompt (spec v6 §5): the candidate has already passed the
# mechanical gate, so the verifier scores JUDGMENT ONLY and must not re-check
# structure. Weights here mirror VERIFIER_WEIGHTS (coverage 0.35 / dependency 0.20).
VERIFIER_SYSTEM = """
You are a plan verifier. You receive a goal and ONE candidate plan that has already
passed mechanical validation: no cycles, no dangling or duplicate ids, every subtask
feeds the synthesis node, and success_criteria are present. Do NOT re-check any of
that; it is guaranteed. Score only judgment, each 0.0-1.0:

- coverage: do the subtasks together satisfy the goal with no gaps?
- decomposition_soundness: right granularity — genuinely separable subtasks, not too
  coarse, not atomized?
- dependency_correctness: are the stated depends_on edges real, and are any needed
  edges missing? (acyclicity is guaranteed; do not comment on it)
- tier_appropriateness: are the tier_hints reasonable for each subtask's difficulty?
- verifiability: is each success_criteria set actually checkable, not just present?

Report a fatal flaw in `fatal_flaws` ONLY for: a goal requirement that no subtask
covers. Do NOT report cycles, missing ids, or unreachable subtasks — those are the
mechanical gate's job, not yours.

Give a two-or-three sentence `rationale` and a single `repair_hint` (the most useful
change if the score is low). Do NOT compute a total; the caller weights the scores.
Respond with ONLY the VerifierVerdict JSON.
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
                     n: int | None = None, replan_budget: int = 1,
                     tau: float | None = None, feedback: str | None = None) -> TaskGraph:
    """Best-of-N decompose-and-verify. Returns the selected plan; broadcasts
    graph_ready for the winner. Raises PlanningFailed if nothing survives.

    N_CANDIDATES (config knob, default 3 — spec v6 §4) sets how many strategy-
    seeded candidates to generate. Each survives a deterministic mechanical gate
    before any verifier call is spent; the generative verifier scores only the
    survivors on judgment dimensions.
    """
    from backend.graph.swarm_graph import validate_task_graph  # lazy: avoid cycle

    if tau is None:
        tau = float(os.getenv("PLAN_TAU", "0.6"))
    pool = _plan_strategies()
    n = _n_candidates(n)
    seeds = (pool * (n // len(pool) + 1))[:n]

    candidates = await asyncio.gather(
        *[plan_candidate(query, run_id, s, critique=feedback) for s in seeds])

    # Mechanical gate (spec v6 §5): hard-exclude structurally invalid candidates
    # with no LLM call, BEFORE spending verifier tokens. strict=True also requires
    # success_criteria presence (the planner contract).
    survivors = []
    for c in candidates:
        gate = validate_task_graph(c, strict=True)
        if gate.ok:
            survivors.append(c)
        else:
            logger.info("[plan_phase] candidate %s failed mechanical gate: %s",
                        c.plan_id, "; ".join(gate.errors))

    if not survivors:
        if replan_budget > 0:
            logger.info("[plan_phase] no candidate passed the mechanical gate — replanning")
            return await plan_phase(query, run_id, broadcast, n=n,
                                    replan_budget=replan_budget - 1, tau=tau,
                                    feedback="all candidates were structurally invalid "
                                             "(cycle, orphan subtask, missing/duplicate "
                                             "id, or missing success_criteria)")
        raise PlanningFailed("no candidate plan passed the mechanical gate")

    verdicts = await asyncio.gather(
        *[verify_plan(query, c, run_id) for c in survivors])

    scored = [(c, weighted_total(v.scores), v)
              for c, v in zip(survivors, verdicts) if not v.fatal_flaws]

    if not scored:
        if replan_budget > 0:
            hint = next((v.repair_hint for v in verdicts if v.repair_hint),
                        "every candidate had a goal-coverage fatal flaw")
            logger.info("[plan_phase] all survivors fatally flawed — replanning")
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
                                    "n_candidates": n,            # recorded for sweeps
                                    "n_survivors": len(survivors),
                                    "rationale": best_verdict.rationale}}))
    return best_plan
