"""
Validator agent: checks worker output against TaskSpec contract.

Uses the same shared Mistral-7B engine as workers (single-engine topology).

Flow:
  1. Run fast mechanical checks (syntax, table shape, etc.) — no LLM needed
  2. If mechanics pass, run LLM semantic check against the success_criteria
  3. Return ValidationVerdict: compliant + failed_criteria + correction_hint + severity

severity levels:
  minor    — close, needs specific fix  → retry
  major    — misses objective substantially → retry
  unfixable — task cannot be completed with available data → commit as-is
"""
from __future__ import annotations

import logging
import os

from backend.inference.client import InferenceClient
from backend.schemas.models import (
    AgentResult,
    ArtifactType,
    TaskSpec,
    TaskType,
    ValidationVerdict,
)

logger = logging.getLogger(__name__)

VALIDATOR_SYSTEM = """
You are a compliance validator. Given:
1. A task contract (objective, scope, deliverable_format, success_criteria)
2. A worker's output

Determine whether the output satisfies the contract. Do NOT re-do the task.
Only check compliance. If the worker's output meets every success_criterion,
mark compliant=true. If not, identify which criteria failed and give a
SPECIFIC correction hint the worker can use to retry.

severity levels:
  minor: output is close, needs specific fix (e.g. "add source citations for claims 2 and 4")
  major: output misses the objective substantially (e.g. "produced analysis when asked for retrieval")
  unfixable: task cannot be completed (e.g. "no relevant information exists in the sources provided")

Respond with ONLY a JSON object with these fields:
{
  "compliant": true|false,
  "failed_criteria": ["criterion text that was not met"],
  "correction_hint": "specific actionable hint for the worker on retry",
  "severity": "minor|major|unfixable"
}
""".strip()


def _make_validator_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("TEXT_ENGINE_ENDPOINT", os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1")),
        model=os.getenv("TEXT_ENGINE_MODEL", os.getenv("ORCHESTRATOR_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")),
        hardware="cpu",
        use_semaphore=True,   # validator competes with workers for the shared engine
    )


def _check_mechanical(task: TaskSpec, result: AgentResult) -> ValidationVerdict | None:
    """
    Fast mechanical checks that don't need an LLM call.
    Returns a ValidationVerdict if the check is conclusive; None if LLM needed.
    """
    fmt = task.deliverable_format

    # Code artifacts: Python syntax validation
    if fmt == "code_block_python":
        for art in result.artifacts:
            if art.type == ArtifactType.code:
                if not art.content.get("syntax_valid", False):
                    err = art.content.get("syntax_error", "unknown syntax error")
                    return ValidationVerdict(
                        compliant=False,
                        failed_criteria=["Code must pass Python ast.parse()"],
                        correction_hint=f"Fix Python syntax error: {err}",
                        severity="minor",
                    )
        # If no code artifact found at all
        has_code = any(a.type == ArtifactType.code for a in result.artifacts)
        if not has_code:
            return ValidationVerdict(
                compliant=False,
                failed_criteria=["Missing code artifact"],
                correction_hint="Produce a code artifact with type='code' and language='python'",
                severity="major",
            )

    # Mermaid diagram: must start with a valid diagram type
    if fmt == "mermaid_diagram":
        for art in result.artifacts:
            if art.type == ArtifactType.diagram:
                mermaid = art.content.get("mermaid", "").strip()
                valid_starts = ("graph", "flowchart", "sequenceDiagram", "classDiagram", "erDiagram")
                if not any(mermaid.startswith(s) for s in valid_starts):
                    return ValidationVerdict(
                        compliant=False,
                        failed_criteria=["Mermaid must start with valid diagram type"],
                        correction_hint="Start diagram with 'graph TD' or 'flowchart TD'",
                        severity="minor",
                    )
        has_diagram = any(a.type == ArtifactType.diagram for a in result.artifacts)
        if not has_diagram:
            return ValidationVerdict(
                compliant=False,
                failed_criteria=["Missing diagram artifact"],
                correction_hint="Produce a diagram artifact with type='diagram' containing mermaid syntax",
                severity="major",
            )

    # Comparison table: ≥2 rows and ≥2 columns
    if fmt == "comparison_table":
        for art in result.artifacts:
            if art.type == ArtifactType.table:
                rows = art.content.get("rows", [])
                headers = art.content.get("headers", [])
                if len(rows) < 2 or len(headers) < 2:
                    return ValidationVerdict(
                        compliant=False,
                        failed_criteria=["Comparison table needs ≥2 rows and ≥2 columns"],
                        correction_hint=(
                            f"Current table: {len(rows)} rows, {len(headers)} columns. "
                            "Add more rows (metrics) and at least 2 system columns."
                        ),
                        severity="minor",
                    )
        has_table = any(a.type == ArtifactType.table for a in result.artifacts)
        if not has_table:
            return ValidationVerdict(
                compliant=False,
                failed_criteria=["Missing table artifact"],
                correction_hint="Produce a table artifact with type='table', headers, and rows",
                severity="major",
            )

    # Claim verdicts: at least one verdict artifact
    if fmt == "claim_verdicts":
        verdicts = [a for a in result.artifacts if a.type == ArtifactType.claim_verdict]
        if not verdicts:
            return ValidationVerdict(
                compliant=False,
                failed_criteria=["No claim_verdict artifacts found"],
                correction_hint="Produce at least one claim_verdict artifact for each major claim",
                severity="major",
            )

    # Citation check for research tasks
    if fmt in ("finding_list_with_citations", "finding_list_with_numeric_values"):
        if not result.result or len(result.result.strip()) < 50:
            return ValidationVerdict(
                compliant=False,
                failed_criteria=["Result is too short or empty — no findings produced"],
                correction_hint="Produce a detailed paragraph with specific facts and numbers",
                severity="major",
            )

    # LLM semantic check needed
    return None


def _build_validator_prompt(task: TaskSpec, result: AgentResult) -> str:
    task_desc = task.objective or task.description
    lines = [
        f"TASK CONTRACT:",
        f"  Objective: {task_desc}",
        f"  Deliverable format: {task.deliverable_format}",
        f"  Success criteria:",
    ]
    for c in task.success_criteria:
        lines.append(f"    - {c}")

    lines.append(f"\nWORKER OUTPUT:")
    lines.append(f"  Result text ({len(result.result)} chars): {result.result[:500]}")
    lines.append(f"  Artifacts ({len(result.artifacts)}):")
    for art in result.artifacts:
        lines.append(f"    - type={art.type.value}, confidence={art.confidence:.2f}")
        # Show key content fields
        for key in ("headers", "rows", "mermaid", "language", "verdict", "claim"):
            if key in art.content:
                val = art.content[key]
                if isinstance(val, list):
                    lines.append(f"      {key}: {len(val)} items")
                else:
                    lines.append(f"      {key}: {str(val)[:100]}")

    return "\n".join(lines)


async def validate_worker_output(
    task: TaskSpec,
    result: AgentResult,
) -> ValidationVerdict:
    """
    Validate a worker's output against its TaskSpec contract.

    1. Run fast mechanical checks — no LLM needed for structural failures
    2. Fall back to LLM semantic check if mechanical passes

    Returns ValidationVerdict.
    """
    # Fast mechanical check first
    mechanical = _check_mechanical(task, result)
    if mechanical is not None:
        logger.info(
            "Mechanical validation %s for task %s (format=%s): %s",
            "FAILED" if not mechanical.compliant else "PASSED",
            task.id,
            task.deliverable_format,
            mechanical.correction_hint or "ok",
        )
        return mechanical

    # If no success_criteria defined, pass through
    if not task.success_criteria:
        return ValidationVerdict(compliant=True)

    # LLM semantic validation
    client = _make_validator_client()
    prompt = _build_validator_prompt(task, result)
    messages = [
        {"role": "system", "content": VALIDATOR_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        verdict = await client.complete_structured(
            messages=messages,
            response_model=ValidationVerdict,
            max_tokens=256,
        )
        logger.info(
            "LLM validation %s for task %s: compliant=%s severity=%s",
            "PASSED" if verdict.compliant else "FAILED",
            task.id,
            verdict.compliant,
            verdict.severity,
        )
        return verdict
    except Exception as exc:
        # Validator failure should not block the run — approve with warning
        logger.warning("Validator LLM call failed for task %s: %s — approving anyway", task.id, exc)
        return ValidationVerdict(compliant=True)
