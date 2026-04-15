"""
Reducer agent: synthesizes all worker AgentResults into a final answer.
Uses the orchestrator endpoint (stronger model).
"""
import os
import time
from datetime import datetime

from backend.inference.client import InferenceClient
from backend.schemas.models import (
    AgentResult,
    SwarmState,
    TaskStatus,
    EventType,
    SwarmEvent,
)

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


async def synthesize(
    query: str,
    results: dict[str, AgentResult],
    task_graph,
    run_id: str,
    broadcast,
) -> str:
    """Synthesize all results into a final answer, emitting WebSocket events."""
    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.synthesis_started,
            run_id=run_id,
            payload={"task_count": len(results)},
        ),
    )

    client = _make_client()
    synthesis_prompt = _build_synthesis_prompt(query, results, task_graph)
    messages = [
        {"role": "system", "content": REDUCER_SYSTEM},
        {"role": "user", "content": synthesis_prompt},
    ]

    final_answer, latency_ms = await client.complete(messages, max_tokens=1024)

    # Include attribution footer
    attributions = []
    for task in task_graph.tasks:
        r = results.get(task.id)
        if r and r.status == TaskStatus.completed:
            attributions.append(
                f"- **{task.description}** → {r.model_used} ({r.hardware}, {r.latency_ms:.0f}ms, conf={r.confidence:.2f})"
            )
    if attributions:
        final_answer += "\n\n---\n**Agent attribution:**\n" + "\n".join(attributions)

    return final_answer


async def reduce(state: SwarmState) -> SwarmState:
    """LangGraph node: synthesize results and mark run complete."""
    async def _noop(run_id, event):
        pass

    final_answer = await synthesize(
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
