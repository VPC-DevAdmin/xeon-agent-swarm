"""
Orchestrator agent: decomposes a user query into a TaskGraph.
Uses instructor + complete_structured() to guarantee valid JSON output.
"""
import os
from backend.inference.client import InferenceClient
from backend.schemas.models import TaskGraph, SwarmState, EventType, SwarmEvent

ORCHESTRATOR_SYSTEM = """
You are a query decomposition specialist for an AI agent swarm.
Break the user's query into 5-8 subtasks executed by specialist agents.

AVAILABLE TASK TYPES:
  research    — retrieve factual information from the knowledge corpus and web
  analysis    — compare, contrast, evaluate, or reason over research findings
  code        — write working code snippets or examples relevant to the query
  vision      — analyze images or diagrams from the knowledge corpus
  fact_check  — verify key claims against the corpus; flag uncertain assertions
  writing     — synthesize ALL prior results into a structured intelligence report
  general     — catch-all for tasks that don't fit the above

MANDATORY RULES:
1. Always include exactly ONE "writing" task. It must depend on ALL other task IDs.
2. Include ONE "fact_check" task that depends on at least one research task.
3. Include 2-3 "research" tasks covering different facets of the query.
4. Include ONE "analysis" task that depends on the research tasks.
5. Include ONE "code" task if the query has any technical / implementation angle.
6. Include ONE "vision" task if diagrams, architecture images, or visual comparisons are relevant.
7. Research tasks have NO dependencies (run immediately in parallel).
8. The writing task must be last — it depends on fact_check, analysis, and any other non-research tasks.

DEPENDENCY EXAMPLE for "Compare Intel Xeon and NVIDIA H100 for LLM inference":
  t1: research — Intel Xeon AMX capabilities for AI inference  (deps: [])
  t2: research — NVIDIA H100 architecture and memory bandwidth  (deps: [])
  t3: research — Latest benchmarks: CPU vs GPU for LLM inference  (deps: [])
  t4: analysis — Compare Xeon vs H100 tradeoffs: cost, power, throughput  (deps: [t1, t2, t3])
  t5: code     — Python example using vLLM on CPU with OpenVINO backend  (deps: [t1])
  t6: vision   — Analyze architecture diagram of transformer attention on hardware  (deps: [])
  t7: fact_check — Verify performance claims from research  (deps: [t1, t2, t3])
  t8: writing  — Full intelligence report on CPU vs GPU LLM inference  (deps: [t4, t5, t6, t7])

Output JSON:
{
  "query": "<original query>",
  "reasoning": "<2-3 sentences on your decomposition strategy>",
  "tasks": [
    {"id": "t1", "description": "...", "type": "research", "dependencies": [], "priority": 1},
    ...
  ]
}
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
        max_tokens=1536,
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
        max_tokens=1536,
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
