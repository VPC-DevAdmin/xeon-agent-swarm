"""
LangGraph swarm graph — fan-out / fan-in pattern with optional validation.

Graph structure (validator enabled):

  [orchestrate] → [validate_graph] → fan-out → [worker_0] ─┐
                                               [worker_1]   ├→ [reduce] → END
                                               [worker_N] ──┘

Each worker optionally goes through a validate+retry loop before committing
its result to state.results. The validate_graph node checks structural rules
before any workers start and retries orchestration once on failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from backend.schemas.models import (
    AgentResult,
    SwarmState,
    TaskGraph,
    TaskSpec,
    TaskStatus,
    TaskType,
    DELIVERABLE_FORMATS,
)
from backend.agents.orchestrator import orchestrate
from backend.agents.worker import run_worker
from backend.agents.reducer import reduce

logger = logging.getLogger(__name__)


# ── Graph validation (rules-based, no LLM) ───────────────────────────────────

@dataclass
class GraphValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)

    def critique(self) -> str:
        return "\n".join(f"- {e}" for e in self.errors)


def _has_cycle(tasks: list[TaskSpec]) -> bool:
    """DFS-based cycle detection on the dependency graph."""
    graph = {t.id: list(t.dependencies) for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in graph}

    def dfs(tid: str) -> bool:
        if tid not in color:
            return False
        if color[tid] == GRAY:
            return True
        if color[tid] == BLACK:
            return False
        color[tid] = GRAY
        for dep in graph.get(tid, []):
            if dfs(dep):
                return True
        color[tid] = BLACK
        return False

    return any(dfs(tid) for tid in list(graph) if color.get(tid) == WHITE)


def validate_task_graph(tg: TaskGraph) -> GraphValidationResult:
    """
    Structural validation rules for a TaskGraph.
    All rules are deterministic — no LLM call needed.
    """
    errors: list[str] = []
    tasks = tg.tasks
    all_ids = {t.id for t in tasks}

    # Rule 1: exactly one writing task
    writing = [t for t in tasks if t.type == TaskType.writing]
    if len(writing) != 1:
        errors.append(f"Expected exactly 1 writing task, found {len(writing)}")
    elif writing:
        # Rule 2: writing task depends on all others
        other_ids = {t.id for t in tasks if t.id != writing[0].id}
        missing = other_ids - set(writing[0].dependencies)
        if missing:
            errors.append(
                f"Writing task '{writing[0].id}' must depend on all others; "
                f"missing deps: {sorted(missing)}"
            )

    # Rule 3: at least one research task
    research = [t for t in tasks if t.type == TaskType.research]
    if not research:
        errors.append("At least one research task is required")

    # Rule 4: every analysis task depends on at least one research task
    research_ids = {t.id for t in research}
    for t in tasks:
        if t.type == TaskType.analysis:
            if not any(dep in research_ids for dep in t.dependencies):
                errors.append(
                    f"Analysis task '{t.id}' must depend on at least one research task"
                )

    # Rule 5: every fact_check task depends on at least one research task
    for t in tasks:
        if t.type == TaskType.fact_check:
            if not any(dep in research_ids for dep in t.dependencies):
                errors.append(
                    f"Fact_check task '{t.id}' must depend on at least one research task"
                )

    # Rule 6: no cycles
    if _has_cycle(tasks):
        errors.append("Dependency graph contains a cycle")

    # Rule 7: all dependency IDs exist
    for t in tasks:
        for dep in t.dependencies:
            if dep not in all_ids:
                errors.append(f"Task '{t.id}' depends on unknown task '{dep}'")

    # Rule 8: deliverable_format is a known value (if set)
    for t in tasks:
        if t.deliverable_format and t.deliverable_format not in DELIVERABLE_FORMATS:
            errors.append(
                f"Task '{t.id}' has unknown deliverable_format: '{t.deliverable_format}'"
            )

    return GraphValidationResult(valid=len(errors) == 0, errors=errors)


async def validate_graph_node(state: SwarmState) -> SwarmState:
    """
    LangGraph node: validate the task graph produced by the orchestrator.
    On failure, clears task_graph so the router retries orchestration.
    """
    if state.task_graph is None:
        return state

    result = validate_task_graph(state.task_graph)
    if result.valid:
        logger.info("Task graph validation passed (%d tasks)", len(state.task_graph.tasks))
    else:
        logger.warning(
            "Task graph validation failed (attempt %d): %s",
            state.orchestrator_retries + 1,
            result.critique(),
        )
        state.task_graph = None  # trigger re-orchestration
        state.orchestrator_retries += 1

    return state


def should_retry_orchestration(state: SwarmState) -> str:
    """Router after validate_graph: retry orchestrate or proceed to workers."""
    if state.task_graph is None:
        if state.orchestrator_retries < 2:
            logger.info("Retrying orchestration (attempt %d)", state.orchestrator_retries + 1)
            return "orchestrate"
        else:
            logger.error("Orchestration failed after 2 retries — proceeding to reduce")
            return "reduce"
    return "route_tasks_entry"


# ── Task routing ──────────────────────────────────────────────────────────────

def route_tasks(state: SwarmState):
    """
    After orchestration (or after each worker), emit a Send for every task
    whose dependencies are *successfully* completed.  Tasks whose upstream
    dependencies failed or were killed are cascade-failed immediately (their
    result is recorded with status=failed, no worker invocation) — this
    prevents the reducer from synthesising a report over fabricated context.
    Returns "reduce" when no more tasks can advance.
    """
    if state.task_graph is None:
        return "reduce"

    # Terminal = reached some final state (won't change anymore)
    terminal_ids = {
        tid for tid, r in state.results.items()
        if r.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.killed)
    }
    # Successful = the only kind of dependency a child should actually run on
    successful_ids = {
        tid for tid, r in state.results.items()
        if r.status == TaskStatus.completed
    }

    # Tasks not yet started whose deps have all reached a terminal state
    ready = [
        t
        for t in state.task_graph.tasks
        if t.id not in state.results
        and all(dep in terminal_ids for dep in t.dependencies)
    ]

    to_run: list[TaskSpec] = []
    for t in ready:
        failed_deps = [d for d in t.dependencies if d not in successful_ids]
        if failed_deps:
            # Cascade-fail: mark the task failed without invoking a worker.
            # Mutating state.results directly from a routing function is safe
            # here because Send() is returned afterwards and LangGraph merges
            # the mutation before the next superstep.
            logger.warning(
                "Cascade-failing %s — upstream failed: %s",
                t.id, failed_deps,
            )
            state.results[t.id] = AgentResult(
                task_id=t.id,
                status=TaskStatus.failed,
                result=(
                    f"Upstream dependency failed: {', '.join(failed_deps)}. "
                    "Task skipped — downstream synthesis would be ungrounded."
                ),
                confidence=0.0,
                model_used="n/a",
                hardware="n/a",
                latency_ms=0.0,
            )
        else:
            to_run.append(t)

    if to_run:
        return [Send("worker", {"task": t, "state": state}) for t in to_run]

    # Nothing new to schedule. If everything is terminal, go to reduce; else
    # we're waiting on in-flight workers and this routing pass is a no-op.
    all_terminal = all(
        t.id in state.results for t in state.task_graph.tasks
    )
    if all_terminal:
        return "reduce"
    return []


def _route_tasks_entry(state: SwarmState):
    """Entry point for routing after validate_graph passes."""
    return route_tasks(state)


def build_swarm_graph():
    graph = StateGraph(SwarmState)

    graph.add_node("orchestrate", orchestrate)
    graph.add_node("validate_graph", validate_graph_node)
    graph.add_node("route_tasks_entry", lambda s: s)  # pass-through for conditional routing
    graph.add_node("worker", run_worker)
    graph.add_node("reduce", reduce)

    graph.set_entry_point("orchestrate")
    graph.add_edge("orchestrate", "validate_graph")
    graph.add_conditional_edges(
        "validate_graph",
        should_retry_orchestration,
        {
            "orchestrate": "orchestrate",
            "reduce": "reduce",
            "route_tasks_entry": "route_tasks_entry",
        },
    )
    graph.add_conditional_edges("route_tasks_entry", route_tasks)
    graph.add_conditional_edges("worker", route_tasks)
    graph.add_edge("reduce", END)

    return graph.compile()
