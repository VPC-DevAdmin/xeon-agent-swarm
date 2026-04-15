"""
A/B baseline agent: sends the original query directly to a single large model
without decomposition. Streams tokens back via WebSocket as single_token events.
"""
import os
import time

from backend.inference.client import InferenceClient
from backend.schemas.models import (
    SingleModelResult,
    TaskStatus,
    EventType,
    SwarmEvent,
)

SINGLE_MODEL_SYSTEM = """
You are a helpful assistant. Answer the user's question thoroughly and accurately.
Use markdown formatting for readability where appropriate.
""".strip()


def _make_client() -> InferenceClient:
    return InferenceClient(
        base_url=os.getenv("SINGLE_MODEL_ENDPOINT", "http://localhost:8083/v1"),
        model=os.getenv("SINGLE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        hardware="cpu",
    )


async def run_single_model(
    run_id: str,
    query: str,
    broadcast,
) -> SingleModelResult:
    """
    Stream the single-model response, emitting events for the A/B panel.
    Runs concurrently with the swarm pipeline.
    """
    client = _make_client()
    messages = [
        {"role": "system", "content": SINGLE_MODEL_SYSTEM},
        {"role": "user", "content": query},
    ]

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.single_started,
            run_id=run_id,
            payload={"model": client.model, "hardware": client.hardware},
        ),
    )

    t0 = time.perf_counter()
    full_answer = ""

    async for token in client.stream(messages, max_tokens=1024):
        full_answer += token
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.single_token,
                run_id=run_id,
                payload={"token": token},
            ),
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    result = SingleModelResult(
        run_id=run_id,
        query=query,
        answer=full_answer,
        model_used=client.model,
        hardware=client.hardware,
        latency_ms=latency_ms,
        status=TaskStatus.completed,
    )

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.single_completed,
            run_id=run_id,
            payload={
                "answer": full_answer,
                "model_used": client.model,
                "hardware": client.hardware,
                "latency_ms": latency_ms,
            },
        ),
    )

    return result
