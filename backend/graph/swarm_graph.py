"""
LangGraph swarm graph — fan-out / fan-in pattern.

Graph structure:

  [orchestrate] → fan-out → [worker_0] ─┐
                            [worker_1]   ├→ [reduce] → END
                            [worker_N] ──┘

LangGraph's Send API handles dynamic fan-out based on the task graph.
Dependencies are resolved: tasks with no deps start immediately; tasks with
deps wait until all dependencies have AgentResult entries in state.results.
"""
from langgraph.graph import StateGraph, END
from langgraph.types import Send

from backend.schemas.models import SwarmState, TaskStatus
from backend.agents.orchestrator import orchestrate
from backend.agents.worker import run_worker
from backend.agents.reducer import reduce


def route_tasks(state: SwarmState):
    """
    After orchestration (or after each worker), emit a Send for every task
    whose dependencies are already satisfied. Returns "reduce" when all tasks
    are complete.
    """
    if state.task_graph is None:
        return "reduce"

    completed_ids = {
        tid
        for tid, r in state.results.items()
        if r.status == TaskStatus.completed
    }

    # Tasks not yet started and whose dependencies are all satisfied
    pending = [
        t
        for t in state.task_graph.tasks
        if t.id not in state.results
        and all(dep in completed_ids for dep in t.dependencies)
    ]

    if not pending:
        return "reduce"

    return [Send("worker", {"task": t, "state": state}) for t in pending]


def build_swarm_graph():
    graph = StateGraph(SwarmState)

    graph.add_node("orchestrate", orchestrate)
    graph.add_node("worker", run_worker)
    graph.add_node("reduce", reduce)

    graph.set_entry_point("orchestrate")
    graph.add_conditional_edges("orchestrate", route_tasks)
    graph.add_conditional_edges("worker", route_tasks)  # re-evaluate after each worker
    graph.add_edge("reduce", END)

    return graph.compile()
