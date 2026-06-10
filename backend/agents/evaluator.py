"""Per-step evaluator (decompose-verify spec v3 §6).

Scores one subtask's output against its output_contract and returns a StepEvalVerdict
(pass/score/reason/fix_hint). A failed verdict drives the worker's tier escalation:
the orchestrator retries the subtask with metadata.min_tier bumped one tier above
the tier the worker was just served at.

Pinned role (model="evaluator"), low-temp, thinking ON — a consistent judge.
"""
from __future__ import annotations

import logging

from backend.inference.client import InferenceClient, llm_endpoint, llm_model_for
from backend.observability.trace import trace_headers
from backend.schemas.models import AgentResult, StepEvalVerdict, TaskSpec

logger = logging.getLogger(__name__)

EVALUATOR_SYSTEM = """
You are a strict output evaluator. You are given a subtask's success definition
(its output_contract) and a worker's output. Decide whether the output satisfies
the contract.

Return:
- pass: true only if the output meets the contract; false otherwise.
- score: 0.0..1.0, how completely the contract is met.
- reason: one sentence on the deciding factor.
- fix_hint: if not passing, the single most useful change for a retry.

Be grounded in the output_contract — do not reward fluent but off-contract output.
Respond with ONLY the StepEvalVerdict JSON.
""".strip()


def _evaluator_client() -> InferenceClient:
    return InferenceClient(base_url=llm_endpoint(), model=llm_model_for("evaluator"),
                           hardware="cpu", use_semaphore=True)


def _contract_for(task: TaskSpec) -> str:
    """The success definition to score against — output_contract, falling back to
    success_criteria / objective so pre-v3 task graphs still evaluate."""
    if task.output_contract:
        return task.output_contract
    if task.success_criteria:
        return "; ".join(task.success_criteria)
    return task.objective or task.description or "Produce a correct, complete result."


async def evaluate_step(task: TaskSpec, result: AgentResult, run_id: str) -> StepEvalVerdict:
    """Score a worker output against the subtask's output_contract."""
    client = _evaluator_client()
    body = result.result or ""
    if result.artifacts:
        body += "\n\n[artifacts]\n" + "\n".join(
            a.model_dump_json() for a in result.artifacts)
    user = (f"OUTPUT_CONTRACT:\n{_contract_for(task)}\n\n"
            f"SUBTASK OBJECTIVE:\n{task.objective or task.description}\n\n"
            f"WORKER OUTPUT:\n{body[:6000]}")
    verdict = await client.complete_structured(
        messages=[{"role": "system", "content": EVALUATOR_SYSTEM},
                  {"role": "user", "content": user}],
        response_model=StepEvalVerdict, max_tokens=400,
        extra_headers=trace_headers(run_id, f"eval:{task.id}"),
        metadata={"run_id": run_id, "step_key": f"eval:{task.id}"},
    )
    verdict.subtask_id = task.id
    return verdict
