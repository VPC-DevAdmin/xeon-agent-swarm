"""
Reducer agent: synthesizes all worker AgentResults into a final answer
and, when a writing task is present, into a structured DocumentResult.

Flow:
  1. Scan completed tasks for a `writing` task
  2. If found: parse its raw JSON output as DocumentResult → run TTS on summary
  3. If not found: fall back to LLM-based synthesis (old behaviour)
  4. Emit synthesis_started / run_completed events either way
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from pydantic import ValidationError

from backend.agents.tts import synthesize_speech
from backend.inference.client import InferenceClient
from backend.schemas.models import (
    AgentResult,
    DocumentResult,
    SwarmState,
    TaskStatus,
    TaskType,
    EventType,
    SwarmEvent,
)

logger = logging.getLogger(__name__)

REDUCER_SYSTEM = """
You are a synthesis specialist. You have received the results of several parallel
specialist agents working on sub-tasks of a user's original query.

Your job is to:
1. Combine the sub-results into a single, coherent, well-structured answer.
2. Resolve any contradictions between agent results (prefer higher-confidence results).
3. Attribute key points to the agents/models that produced them.
4. Produce a final answer that directly addresses the original query.

Be concise but thorough. Use markdown formatting for readability.
""".strip()


def _make_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1"),
        model=os.getenv("ORCHESTRATOR_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        hardware="cpu",
    )


def _build_synthesis_prompt(query: str, results: dict[str, AgentResult], task_graph) -> str:
    lines = [f"Original query: {query}\n"]
    for task in task_graph.tasks:
        result = results.get(task.id)
        if result and result.status == TaskStatus.completed:
            lines.append(
                f"## Subtask: {task.description}\n"
                f"Type: {task.type.value} | Model: {result.model_used} | "
                f"Hardware: {result.hardware} | Confidence: {result.confidence:.2f}\n"
                f"{result.result}\n"
            )
        else:
            lines.append(f"## Subtask: {task.description}\n[FAILED or PENDING]\n")
    return "\n".join(lines)


def _extract_document_result(raw: str) -> DocumentResult | None:
    """
    Try to parse the writing worker's raw output as a DocumentResult.
    Handles JSON wrapped in markdown fences (```json ... ```) gracefully.
    """
    text = raw.strip()
    # Strip markdown fences if the model wrapped the JSON
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    # Find the outermost JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None

    try:
        data = json.loads(text[start:end])
        return DocumentResult(**data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.warning("Failed to parse DocumentResult from writing worker: %s", exc)
        return None


async def synthesize(
    query: str,
    results: dict[str, AgentResult],
    task_graph,
    run_id: str,
    broadcast,
) -> tuple[str, DocumentResult | None]:
    """
    Synthesize all results into a final answer + optional DocumentResult.

    Returns (final_answer_str, document_result_or_None).
    """
    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.synthesis_started,
            run_id=run_id,
            payload={"task_count": len(results)},
        ),
    )

    # ── Path A: writing task produced a DocumentResult ────────────────────────
    document: DocumentResult | None = None
    for task in task_graph.tasks:
        if task.type == TaskType.writing:
            r = results.get(task.id)
            if r and r.status == TaskStatus.completed:
                document = _extract_document_result(r.result)
                if document:
                    logger.info("DocumentResult extracted from writing worker")
                    break

    if document:
        # ── Collect typed artifacts from all workers ───────────────────────────
        # The output panel renders these directly; the writing worker's prose
        # sections are stored separately in document.sections.
        all_artifacts = []
        for task in task_graph.tasks:
            if task.type == TaskType.writing:
                continue   # writing worker produces the document, not artifacts
            r = results.get(task.id)
            if r and r.artifacts:
                all_artifacts.extend(r.artifacts)
        document.artifacts = all_artifacts
        logger.info("Collected %d typed artifacts from %d workers", len(all_artifacts), len(results))

        # ── Validate and patch code_snippets syntax ────────────────────────────
        from backend.schemas.models import validate_code_syntax
        for snip in document.code_snippets:
            snip.syntax_valid = validate_code_syntax(snip.code, snip.language)

        # Run TTS on the executive summary (non-blocking best-effort)
        audio_url = await synthesize_speech(document.executive_summary, run_id)
        if audio_url:
            document.tts_audio_url = audio_url

        # Build a plain-text final_answer from the document for backward compat
        section_texts = "\n\n".join(
            f"### {s.title}\n{s.content}" for s in document.sections
        )
        final_answer = (
            f"# {document.title}\n\n"
            f"**Executive Summary:** {document.executive_summary}\n\n"
            f"{section_texts}"
        )
        if document.key_findings:
            bullets = "\n".join(f"- {f}" for f in document.key_findings)
            final_answer += f"\n\n**Key Findings:**\n{bullets}"

        return final_answer, document

    # ── Path B: no writing task — fall back to LLM synthesis ─────────────────
    client = _make_client()
    synthesis_prompt = _build_synthesis_prompt(query, results, task_graph)
    messages = [
        {"role": "system", "content": REDUCER_SYSTEM},
        {"role": "user", "content": synthesis_prompt},
    ]
    final_answer, _ = await client.complete(messages, max_tokens=1024)

    # Attribution footer
    attributions = []
    for task in task_graph.tasks:
        r = results.get(task.id)
        if r and r.status == TaskStatus.completed:
            attributions.append(
                f"- **{task.description}** → {r.model_used} ({r.hardware}, "
                f"{r.latency_ms:.0f}ms, conf={r.confidence:.2f})"
            )
    if attributions:
        final_answer += "\n\n---\n**Agent attribution:**\n" + "\n".join(attributions)

    return final_answer, None


async def reduce(state: SwarmState) -> SwarmState:
    """LangGraph node: synthesize results and mark run complete."""
    async def _noop(run_id, event):
        pass

    final_answer, _ = await synthesize(
        query=state.query,
        results=state.results,
        task_graph=state.task_graph,
        run_id=state.run_id,
        broadcast=_noop,
    )
    state.final_answer = final_answer
    state.status = TaskStatus.completed
    state.completed_at = datetime.utcnow()
    return state
