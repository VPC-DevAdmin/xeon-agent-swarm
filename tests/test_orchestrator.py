"""
Tests for the orchestrator agent.
Uses a mock InferenceClient to avoid real model calls.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.schemas.models import TaskGraph, TaskSpec, TaskType, SwarmState


@pytest.fixture
def sample_task_graph():
    return TaskGraph(
        query="Compare nuclear and solar energy",
        reasoning="Split into research subtasks that can run in parallel.",
        tasks=[
            TaskSpec(id="t1", description="Research nuclear energy efficiency", type=TaskType.research),
            TaskSpec(id="t2", description="Research solar energy efficiency", type=TaskType.research),
            TaskSpec(id="t3", description="Compare and analyze results", type=TaskType.analysis, dependencies=["t1", "t2"]),
        ],
    )


@pytest.mark.asyncio
async def test_orchestrate_with_events_broadcasts_graph_ready(sample_task_graph):
    """orchestrate_with_events should broadcast a graph_ready event."""
    from backend.agents.orchestrator import orchestrate_with_events

    broadcast_calls = []

    async def mock_broadcast(run_id, event):
        broadcast_calls.append((run_id, event))

    with patch("backend.agents.orchestrator._make_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.complete_structured = AsyncMock(return_value=sample_task_graph)
        mock_client_factory.return_value = mock_client

        result = await orchestrate_with_events(
            query="Compare nuclear and solar energy",
            run_id="test-run-1",
            broadcast=mock_broadcast,
        )

    assert result.query == "Compare nuclear and solar energy"
    assert len(result.tasks) == 3
    assert any(call[1].event.value == "graph_ready" for call in broadcast_calls)


@pytest.mark.asyncio
async def test_orchestrate_returns_task_graph_with_dependencies(sample_task_graph):
    """Tasks with dependencies should be correctly represented."""
    t3 = sample_task_graph.tasks[2]
    assert "t1" in t3.dependencies
    assert "t2" in t3.dependencies


def test_task_graph_serialization(sample_task_graph):
    """TaskGraph should serialize and deserialize cleanly via Pydantic."""
    data = sample_task_graph.model_dump()
    restored = TaskGraph(**data)
    assert restored.query == sample_task_graph.query
    assert len(restored.tasks) == len(sample_task_graph.tasks)


def test_swarm_state_defaults():
    """SwarmState should have sensible defaults."""
    state = SwarmState(query="test query")
    assert state.status.value == "pending"
    assert state.task_graph is None
    assert state.results == {}
    assert state.final_answer is None
    assert state.run_id is not None
