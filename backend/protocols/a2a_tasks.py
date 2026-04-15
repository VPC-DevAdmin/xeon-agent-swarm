"""
A2A task lifecycle state machine.
Task state is stored in Redis so it survives restarts.

State transitions:
  submitted → working → completed | failed
  working   → input_required → working  (if the agent needs clarification)
  any       → canceled
"""
import json
from enum import Enum
from datetime import datetime

import redis.asyncio as aioredis


class A2ATaskState(str, Enum):
    submitted      = "submitted"
    working        = "working"
    input_required = "input_required"
    completed      = "completed"
    failed         = "failed"
    canceled       = "canceled"


_TASK_KEY_PREFIX = "a2a:task:"
_TASK_TTL_SECONDS = 3600  # 1 hour


class A2ATaskManager:
    def __init__(self, redis_url: str):
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    def _key(self, task_id: str) -> str:
        return f"{_TASK_KEY_PREFIX}{task_id}"

    async def create(self, task_id: str, run_id: str, description: str) -> dict:
        record = {
            "task_id": task_id,
            "run_id": run_id,
            "description": description,
            "state": A2ATaskState.submitted.value,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        await self._redis.setex(
            self._key(task_id),
            _TASK_TTL_SECONDS,
            json.dumps(record),
        )
        return record

    async def transition(self, task_id: str, new_state: A2ATaskState, **kwargs) -> dict:
        raw = await self._redis.get(self._key(task_id))
        record = json.loads(raw) if raw else {"task_id": task_id}
        record["state"] = new_state.value
        record["updated_at"] = datetime.utcnow().isoformat()
        record.update(kwargs)
        await self._redis.setex(
            self._key(task_id),
            _TASK_TTL_SECONDS,
            json.dumps(record),
        )
        return record

    async def get(self, task_id: str) -> dict | None:
        raw = await self._redis.get(self._key(task_id))
        return json.loads(raw) if raw else None

    async def list_for_run(self, run_id: str) -> list[dict]:
        # Scan is acceptable at demo scale
        keys = await self._redis.keys(f"{_TASK_KEY_PREFIX}*")
        tasks = []
        for key in keys:
            raw = await self._redis.get(key)
            if raw:
                record = json.loads(raw)
                if record.get("run_id") == run_id:
                    tasks.append(record)
        return tasks
