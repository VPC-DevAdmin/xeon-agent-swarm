"""
Orchestrator agent: decomposes a user query into a TaskGraph.
Uses instructor + complete_structured() to guarantee valid JSON output.
"""
import os
from backend.inference.client import InferenceClient
from backend.schemas.models import TaskGraph, SwarmState, EventType, SwarmEvent

ORCHESTRATOR_SYSTEM = """
You are a query decomposition specialist. Break the user's query into 3-6 independent
subtasks that can be executed in parallel by specialist agents.

For each task, assign:
- A short description (one sentence)
- A type: research | analysis | code | summarization | general
- Dependencies: list of task IDs that must complete before this one (empty = can start immediately)
- Priority: 1 (normal) or 2 (high)

Output a JSON object with:
{
  "query": "<original query>",
  "reasoning": "<1-2 sentences explaining your decomposition strategy>",
  "tasks": [...]
}

Keep tasks focused and independent. Prefer parallel tasks over sequential chains.
If the query is simple, use just 1-2 tasks.
""".strip()


def _make_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1"),
        model=os.getenv("ORCHESTRATOR_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        hardware="cpu",
    )


async def orchestrate(state: SwarmState) -> SwarmState:
    """LangGraph node: decompose query into TaskGraph and update state."""
    client = _make_client()
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": state.query},
    ]
    task_graph: TaskGraph = await client.complete_structured(
        messages=messages,
        response_model=TaskGraph,
        max_tokens=1024,
    )
    state.task_graph = task_graph
    return state


async def orchestrate_with_events(
    query: str,
    run_id: str,
    broadcast,
) -> TaskGraph:
    """
    Stand-alone helper used by main.py: decomposes query and broadcasts graph_ready.
    Returns the TaskGraph.
    """
    client = _make_client()
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": query},
    ]
    task_graph: TaskGraph = await client.complete_structured(
        messages=messages,
        response_model=TaskGraph,
        max_tokens=1024,
    )
    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.graph_ready,
            run_id=run_id,
            payload=task_graph.model_dump(),
        ),
    )
    return task_graph
