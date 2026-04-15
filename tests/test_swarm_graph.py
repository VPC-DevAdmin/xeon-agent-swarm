"""
Tests for the LangGraph swarm graph routing logic.
"""
import pytest
from backend.schemas.models import (
    SwarmState,
    TaskGraph,
    TaskSpec,
    TaskType,
    AgentResult,
    TaskStatus,
)
from backend.graph.swarm_graph import route_tasks


def make_state(tasks, results=None):
    state = SwarmState(query="test")
    state.task_graph = TaskGraph(
        query="test",
        tasks=tasks,
        reasoning="test decomposition",
    )
    state.results = results or {}
    return state


def make_result(task_id, status=TaskStatus.completed):
    return AgentResult(
        task_id=task_id,
        status=status,
        result="result text",
        confidence=0.9,
        model_used="test-model",
        hardware="cpu",
        latency_ms=100.0,
    )


def test_route_tasks_all_independent():
    """All tasks with no dependencies should be dispatched immediately."""
    tasks = [
        TaskSpec(id="t1", description="Task 1", type=TaskType.research),
        TaskSpec(id="t2", description="Task 2", type=TaskType.analysis),
        TaskSpec(id="t3", description="Task 3", type=TaskType.general),
    ]
    state = make_state(tasks)
    result = route_tasks(state)

    assert isinstance(result, list)
    assert len(result) == 3


def test_route_tasks_respects_dependencies():
    """Tasks with unsatisfied dependencies should not be dispatched."""
    tasks = [
        TaskSpec(id="t1", description="Task 1", type=TaskType.research),
        TaskSpec(id="t2", description="Task 2", type=TaskType.analysis, dependencies=["t1"]),
    ]
    state = make_state(tasks)
    result = route_tasks(state)

    # Only t1 should be dispatched (t2 depends on t1)
    assert isinstance(result, list)
    assert len(result) == 1


def test_route_tasks_dispatches_dependent_after_dep_completes():
    """Once a dependency completes, the dependent task becomes ready."""
    tasks = [
        TaskSpec(id="t1", description="Task 1", type=TaskType.research),
        TaskSpec(id="t2", description="Task 2", type=TaskType.analysis, dependencies=["t1"]),
    ]
    # t1 is already completed
    state = make_state(tasks, results={"t1": make_result("t1")})
    result = route_tasks(state)

    assert isinstance(result, list)
    assert len(result) == 1  # t2 now ready


def test_route_tasks_returns_reduce_when_all_complete():
    """Returns 'reduce' when all tasks are done."""
    tasks = [
        TaskSpec(id="t1", description="Task 1", type=TaskType.research),
        TaskSpec(id="t2", description="Task 2", type=TaskType.analysis),
    ]
    state = make_state(
        tasks,
        results={
            "t1": make_result("t1"),
            "t2": make_result("t2"),
        },
    )
    result = route_tasks(state)
    assert result == "reduce"


def test_route_tasks_no_graph_returns_reduce():
    """If task_graph is None, returns 'reduce' immediately."""
    state = SwarmState(query="test")
    assert route_tasks(state) == "reduce"


def test_route_tasks_chained_dependencies():
    """t1 → t2 → t3 chain: only t1 ready initially."""
    tasks = [
        TaskSpec(id="t1", description="A", type=TaskType.research),
        TaskSpec(id="t2", description="B", type=TaskType.analysis, dependencies=["t1"]),
        TaskSpec(id="t3", description="C", type=TaskType.summarization, dependencies=["t2"]),
    ]
    state = make_state(tasks)
    result = route_tasks(state)
    assert len(result) == 1  # only t1

    # After t1 completes, t2 becomes ready
    state.results["t1"] = make_result("t1")
    result2 = route_tasks(state)
    assert len(result2) == 1  # only t2

    # After t2 completes, t3 becomes ready
    state.results["t2"] = make_result("t2")
    result3 = route_tasks(state)
    assert len(result3) == 1  # only t3
