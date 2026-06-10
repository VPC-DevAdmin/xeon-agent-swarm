"""Per-step evaluator (decompose-verify spec v6 §6).

Scores one subtask's output against its `success_criteria` and returns a
`ValidationVerdict` (compliant / failed_criteria / correction_hint / severity
+ subtask_id). A non-compliant verdict drives the worker's tier escalation: the
orchestrator retries the subtask with metadata.min_tier bumped one tier above
the tier the worker was just served at. A verdict of severity="unfixable" short-
circuits escalation (more compute won't fix a mis-scoped subtask).

Pinned role (model="evaluator"), low-temp, thinking ON — a consistent judge.
The verdict type is the same `ValidationVerdict` the worker-output validator
uses, so the retry loop consumes one shape (spec v6 retired StepEvalVerdict).
"""
from __future__ import annotations

import logging

from backend.inference.client import InferenceClient, llm_endpoint, llm_model_for
from backend.observability.trace import trace_headers
from backend.schemas.models import AgentResult, TaskSpec, ValidationVerdict

logger = logging.getLogger(__name__)

EVALUATOR_SYSTEM = """
You are a strict output evaluator. You are given a subtask's success_criteria
(the checklist of conditions a correct output must satisfy) and a worker's output.
Decide whether the output satisfies every criterion.

Return:
- compliant: true only if EVERY success criterion is met; false otherwise.
- failed_criteria: the exact criteria (verbatim) that are NOT met. Empty when compliant.
- correction_hint: if not compliant, the single most useful change for a retry.
- severity: "minor" if a retry at a stronger tier would plausibly fix it; "major"
  if the output is substantially off; "unfixable" if no amount of additional
  compute would help (the subtask is mis-scoped, or the needed source/context is
  absent) — re-scoping via replan is the only remedy.

Be grounded in the success_criteria — do not reward fluent but off-criteria output.
Respond with ONLY the ValidationVerdict JSON.
""".strip()


def _evaluator_client() -> InferenceClient:
    return InferenceClient(base_url=llm_endpoint(), model=llm_model_for("evaluator"),
                           hardware="cpu", use_semaphore=True)


def _criteria_for(task: TaskSpec) -> str:
    """The success definition to score against — success_criteria is the single
    authority (spec v6 §3); fall back to objective/description for older graphs."""
    if task.success_criteria:
        return "\n".join(f"- {c}" for c in task.success_criteria)
    return f"- {task.objective or task.description or 'Produce a correct, complete result.'}"


async def evaluate_step(task: TaskSpec, result: AgentResult, run_id: str) -> ValidationVerdict:
    """Score a worker output against the subtask's success_criteria."""
    client = _evaluator_client()
    body = result.result or ""
    if result.artifacts:
        body += "\n\n[artifacts]\n" + "\n".join(
            a.model_dump_json() for a in result.artifacts)
    user = (f"SUCCESS_CRITERIA:\n{_criteria_for(task)}\n\n"
            f"SUBTASK OBJECTIVE:\n{task.objective or task.description}\n\n"
            f"WORKER OUTPUT:\n{body[:6000]}")
    verdict = await client.complete_structured(
        messages=[{"role": "system", "content": EVALUATOR_SYSTEM},
                  {"role": "user", "content": user}],
        response_model=ValidationVerdict, max_tokens=400,
        extra_headers=trace_headers(run_id, f"eval:{task.id}"),
        metadata={"run_id": run_id, "step_key": f"eval:{task.id}"},
    )
    verdict.subtask_id = task.id
    return verdict
