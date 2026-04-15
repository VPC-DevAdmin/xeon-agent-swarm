"""
Generic worker agent: executes a single TaskSpec and returns an AgentResult.
Role (system prompt + tools) is loaded from config/worker_roles.yaml.
"""
import os
import json
import time
import yaml
from pathlib import Path

from backend.inference.client import InferenceClient
from backend.schemas.models import (
    AgentResult,
    SwarmState,
    TaskSpec,
    TaskStatus,
    EventType,
    SwarmEvent,
)
from backend.protocols.mcp_servers import call_tool

_ROLES: dict | None = None


def _load_roles() -> dict:
    global _ROLES
    if _ROLES is None:
        cfg_path = Path(os.getenv("CONFIG_DIR", "/app/config")) / "worker_roles.yaml"
        if not cfg_path.exists():
            # fallback for local dev
            cfg_path = Path(__file__).parent.parent.parent / "config" / "worker_roles.yaml"
        with open(cfg_path) as f:
            _ROLES = yaml.safe_load(f)["roles"]
    return _ROLES


def _select_client(preferred_hardware: str) -> InferenceClient:
    """Choose CPU or GPU endpoint based on role preference and env config."""
    gpu_url = os.getenv("WORKER_GPU_ENDPOINT", "")
    if preferred_hardware == "gpu" and gpu_url:
        return InferenceClient(
            base_url=gpu_url,
            model=os.getenv("WORKER_GPU_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
            hardware="gpu",
        )
    return InferenceClient(
        base_url=os.getenv("WORKER_CPU_ENDPOINT", "http://localhost:8081/v1"),
        model=os.getenv("WORKER_CPU_MODEL", "microsoft/Phi-4-mini-instruct"),
        hardware="cpu",
    )


def _parse_worker_response(raw: str, task_id: str, client: InferenceClient, latency_ms: float) -> AgentResult:
    """Parse the model's JSON response; fall back gracefully to plain text."""
    try:
        data = json.loads(raw)
        result = data.get("result", raw)
        confidence = float(data.get("confidence", 0.8))
    except (json.JSONDecodeError, ValueError):
        result = raw
        confidence = 0.5

    return AgentResult(
        task_id=task_id,
        status=TaskStatus.completed,
        result=result,
        confidence=confidence,
        model_used=client.model,
        hardware=client.hardware,
        latency_ms=latency_ms,
    )


async def execute_task(
    task: TaskSpec,
    run_id: str,
    broadcast,
    context: dict[str, str] | None = None,
) -> AgentResult:
    """Execute a single task, broadcast events, return AgentResult."""
    roles = _load_roles()
    role_cfg = roles.get(task.type.value, roles["general"])
    client = _select_client(role_cfg.get("preferred_hardware", "cpu"))

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.task_started,
            run_id=run_id,
            payload={
                "task_id": task.id,
                "description": task.description,
                "type": task.type.value,
                "model": client.model,
                "hardware": client.hardware,
            },
        ),
    )

    # ── MCP tool calls (inject context before LLM call) ──────────────────────
    tool_context = ""
    tool_calls_made: list[str] = []
    for tool_name in role_cfg.get("tools", []):
        tool_result = await call_tool(tool_name, {"query": task.description})
        if tool_result:
            tool_context += f"\n\n[{tool_name} results]\n{tool_result}"
            tool_calls_made.append(tool_name)

    user_content = task.description
    if tool_context:
        user_content += f"\n\nAdditional context from tools:{tool_context}"
    if context:
        deps_text = "\n".join(f"- {k}: {v}" for k, v in context.items())
        user_content += f"\n\nResults from prerequisite tasks:\n{deps_text}"

    messages = [
        {"role": "system", "content": role_cfg["system_prompt"]},
        {"role": "user", "content": user_content},
    ]

    try:
        raw, latency_ms = await client.complete(messages, max_tokens=768)
        agent_result = _parse_worker_response(raw, task.id, client, latency_ms)
        agent_result.tool_calls = tool_calls_made

        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.task_completed,
                run_id=run_id,
                payload={
                    "task_id": task.id,
                    "result": agent_result.result,
                    "confidence": agent_result.confidence,
                    "model_used": agent_result.model_used,
                    "hardware": agent_result.hardware,
                    "latency_ms": agent_result.latency_ms,
                    "tool_calls": agent_result.tool_calls,
                },
            ),
        )
        return agent_result

    except Exception as exc:
        err_result = AgentResult(
            task_id=task.id,
            status=TaskStatus.failed,
            result=str(exc),
            confidence=0.0,
            model_used=client.model,
            hardware=client.hardware,
            latency_ms=0.0,
        )
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.task_failed,
                run_id=run_id,
                payload={"task_id": task.id, "error": str(exc)},
            ),
        )
        return err_result


async def run_worker(inputs: dict) -> dict:
    """LangGraph node wrapper."""
    task: TaskSpec = inputs["task"]
    state: SwarmState = inputs["state"]
    # In LangGraph context there's no live broadcast; use a no-op
    async def _noop(run_id, event):
        pass

    result = await execute_task(task, state.run_id, _noop)
    state.results[task.id] = result
    return {"results": state.results}
