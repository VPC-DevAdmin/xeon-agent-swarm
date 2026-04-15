"""
Redis pub/sub task queue.

Producers push TaskSpec JSON to a channel; consumers process tasks.
For the demo the queue is mainly used for observability — LangGraph drives
actual execution. The queue lets external observers watch task flow.
"""
import json
import asyncio
import redis.asyncio as aioredis

from backend.schemas.models import TaskSpec, AgentResult

_TASK_CHANNEL = "swarm:tasks"
_RESULT_CHANNEL = "swarm:results"


class TaskQueue:
    def __init__(self, redis_url: str):
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def enqueue_task(self, run_id: str, task: TaskSpec) -> None:
        """Publish a task to the task channel."""
        payload = {"run_id": run_id, "task": task.model_dump()}
        await self._redis.publish(_TASK_CHANNEL, json.dumps(payload))

    async def publish_result(self, run_id: str, result: AgentResult) -> None:
        """Publish a completed AgentResult to the result channel."""
        payload = {"run_id": run_id, "result": result.model_dump()}
        await self._redis.publish(_RESULT_CHANNEL, json.dumps(payload))

    async def subscribe_tasks(self):
        """Async generator yielding (run_id, TaskSpec) tuples."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_TASK_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                yield data["run_id"], TaskSpec(**data["task"])

    async def subscribe_results(self):
        """Async generator yielding (run_id, AgentResult) tuples."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_RESULT_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                yield data["run_id"], AgentResult(**data["result"])

    async def store_run_result(self, run_id: str, result: dict, ttl: int = 3600) -> None:
        """Persist the final RunResult in Redis for GET /run/{run_id}."""
        await self._redis.setex(f"run:{run_id}", ttl, json.dumps(result))

    async def get_run_result(self, run_id: str) -> dict | None:
        raw = await self._redis.get(f"run:{run_id}")
        return json.loads(raw) if raw else None
