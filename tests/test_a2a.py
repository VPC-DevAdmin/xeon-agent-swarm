"""
Tests for A2A protocol layer: Agent Cards and task state machine.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.protocols.a2a_cards import (
    all_agent_cards,
    ORCHESTRATOR_CARD,
    REDUCER_CARD,
    SINGLE_MODEL_CARD,
)
from backend.protocols.a2a_tasks import A2ATaskState, A2ATaskManager


# ── Agent Card tests ──────────────────────────────────────────────────────────

def test_orchestrator_card_structure():
    """Orchestrator card should have required A2A fields."""
    card = ORCHESTRATOR_CARD
    assert card["id"] == "orchestrator-agent"
    assert "decompose" in card["capabilities"]
    assert card["authentication"]["type"] == "none"


def test_reducer_card_has_synthesize_capability():
    assert "synthesize" in REDUCER_CARD["capabilities"]


def test_all_agent_cards_returns_list():
    cards = all_agent_cards()
    assert isinstance(cards, list)
    assert len(cards) >= 3  # orchestrator + reducer + single-model at minimum


def test_all_agent_cards_include_core_agents():
    cards = all_agent_cards()
    ids = [c["id"] for c in cards]
    assert "orchestrator-agent" in ids
    assert "reducer-agent" in ids
    assert "single-model-agent" in ids


def test_worker_cards_have_hardware_field():
    cards = all_agent_cards()
    worker_cards = [c for c in cards if c["id"].startswith("worker-")]
    for card in worker_cards:
        assert "hardware" in card
        assert card["hardware"] in ("cpu", "gpu")


# ── A2A Task State Machine tests ──────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Mock Redis client for A2ATaskManager tests."""
    store = {}

    class MockRedis:
        async def setex(self, key, ttl, value):
            store[key] = value

        async def get(self, key):
            return store.get(key)

        async def keys(self, pattern):
            return list(store.keys())

    return MockRedis(), store


@pytest.mark.asyncio
async def test_a2a_task_create(mock_redis):
    redis_client, store = mock_redis
    manager = A2ATaskManager.__new__(A2ATaskManager)
    manager._redis = redis_client

    record = await manager.create("task-abc", "run-1", "Do some research")
    assert record["task_id"] == "task-abc"
    assert record["state"] == A2ATaskState.submitted.value
    assert "a2a:task:task-abc" in store


@pytest.mark.asyncio
async def test_a2a_task_transition(mock_redis):
    redis_client, store = mock_redis
    manager = A2ATaskManager.__new__(A2ATaskManager)
    manager._redis = redis_client

    await manager.create("task-xyz", "run-2", "Analyze data")
    record = await manager.transition("task-xyz", A2ATaskState.working)
    assert record["state"] == A2ATaskState.working.value

    record2 = await manager.transition(
        "task-xyz", A2ATaskState.completed, result="done"
    )
    assert record2["state"] == A2ATaskState.completed.value
    assert record2["result"] == "done"


@pytest.mark.asyncio
async def test_a2a_task_get(mock_redis):
    redis_client, store = mock_redis
    manager = A2ATaskManager.__new__(A2ATaskManager)
    manager._redis = redis_client

    await manager.create("task-get", "run-3", "Test get")
    fetched = await manager.get("task-get")
    assert fetched is not None
    assert fetched["task_id"] == "task-get"


@pytest.mark.asyncio
async def test_a2a_task_get_nonexistent(mock_redis):
    redis_client, _ = mock_redis
    manager = A2ATaskManager.__new__(A2ATaskManager)
    manager._redis = redis_client

    result = await manager.get("nonexistent-task")
    assert result is None


def test_a2a_task_state_values():
    """All required A2A states should be present."""
    states = {s.value for s in A2ATaskState}
    assert "submitted" in states
    assert "working" in states
    assert "completed" in states
    assert "failed" in states
    assert "canceled" in states
