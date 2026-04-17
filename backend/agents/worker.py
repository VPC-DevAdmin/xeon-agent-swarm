"""
Generic worker agent: executes a single TaskSpec and returns an AgentResult.
Role (system prompt + tools) is loaded from config/worker_roles.yaml.

Vision tasks (TaskType.vision) use a separate code path:
  - Retrieve top-2 images from the Redis image store via caption embedding search
  - Base64-encode images and pass to Phi-3.5-vision via OpenAI vision API format
  - Falls back to text-only analysis if VLM_ENDPOINT is not set
"""
import base64
import os
import json
import time
import yaml
from pathlib import Path

from openai import AsyncOpenAI

from backend.corpus.embedder import Embedder
from backend.corpus.redis_imagestore import RedisImageStore
from backend.corpus.seed_data import CORPORA
from backend.inference.client import InferenceClient
from backend.schemas.models import (
    AgentResult,
    SwarmState,
    TaskSpec,
    TaskStatus,
    TaskType,
    EventType,
    SwarmEvent,
)
from backend.protocols.mcp_servers import call_tool

_IMAGE_DIR = Path(os.getenv("IMAGE_DIR", "/data/images"))

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


async def _retrieve_images(query: str, top_k: int = 2) -> list[dict]:
    """Search all corpus image stores and return the top-k most relevant images."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6479")
    tei_endpoint = os.getenv("TEI_ENDPOINT", "http://tei-embedding:8090")
    emb_dim = int(os.getenv("EMBEDDING_DIM", "384"))

    embedder = Embedder(endpoint=tei_endpoint, dim=emb_dim)
    query_vec = await embedder.embed_one(query)

    all_hits: list[dict] = []
    for corpus_name in CORPORA:
        store = RedisImageStore(redis_url=redis_url, corpus_name=corpus_name, embedding_dim=emb_dim)
        try:
            if await store.index_exists():
                hits = await store.search(query_vec, top_k=top_k)
                all_hits.extend(hits)
        finally:
            await store.close()

    # Sort by ascending cosine distance (lower = more similar), take top_k
    all_hits.sort(key=lambda h: h["score"])
    return all_hits[:top_k]


async def _execute_vision_task(
    task: TaskSpec,
    run_id: str,
    broadcast,
    role_cfg: dict,
) -> AgentResult:
    """
    Vision worker: retrieve relevant images → VLM call with base64 images.
    Falls back to text-only analysis if VLM_ENDPOINT is unset or no images found.
    """
    t0 = time.perf_counter()
    vlm_endpoint = os.getenv("VLM_ENDPOINT", "")
    vlm_model = os.getenv("VLM_MODEL", "microsoft/Phi-3.5-vision-instruct")

    # Retrieve images even if we fall back — useful for debugging
    hits = await _retrieve_images(task.description, top_k=2)

    # Build vision message content
    content: list[dict] = []
    images_used: list[str] = []

    for hit in hits:
        img_path = _IMAGE_DIR / hit["local_path"]
        if img_path.exists():
            raw = img_path.read_bytes()
            b64 = base64.b64encode(raw).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
            content.append({"type": "text", "text": f"[Image: {hit['caption']}]"})
            images_used.append(hit["local_path"])

    content.append({"type": "text", "text": task.description})

    if not vlm_endpoint:
        # VLM service not configured — tell the user what was found and why we fell back.
        # Don't run a text-only model that would just say "no images found"; that's
        # misleading when images were retrieved but we can't send them to the VLM.
        img_count = len(images_used)
        img_note = (
            f"{img_count} relevant image(s) were retrieved from the corpus "
            f"but could not be analyzed because the vision worker (VLM_ENDPOINT) "
            f"is not running. Start vllm-vision with: "
            f"docker compose --profile vision up -d vllm-vision"
            if img_count > 0
            else "No images matched the query and the vision worker (VLM_ENDPOINT) is not running."
        )
        fallback_client = _select_client("cpu")
        latency_ms = (time.perf_counter() - t0) * 1000
        result = AgentResult(
            task_id=task.id,
            status=TaskStatus.completed,
            result=img_note,
            confidence=0.0,
            model_used=fallback_client.model,
            hardware=fallback_client.hardware,
            latency_ms=latency_ms,
        )
        result.tool_calls = ["fallback:no_vlm_endpoint"]
        return result

    if not images_used:
        # VLM is running but no matching images were found in the corpus.
        fallback_client = _select_client("cpu")
        messages = [
            {"role": "system", "content": role_cfg["system_prompt"]},
            {"role": "user", "content": task.description},
        ]
        raw, latency_ms = await fallback_client.complete(messages, max_tokens=512)
        result = _parse_worker_response(raw, task.id, fallback_client, latency_ms)
        result.tool_calls = ["fallback:no_images"]
        return result

    vlm_client = AsyncOpenAI(base_url=vlm_endpoint, api_key="none")
    messages = [
        {"role": "system", "content": role_cfg["system_prompt"]},
        {"role": "user", "content": content},
    ]

    resp = await vlm_client.chat.completions.create(
        model=vlm_model,
        messages=messages,
        max_tokens=512,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    raw_text = resp.choices[0].message.content or ""

    # Parse JSON response
    try:
        data = json.loads(raw_text)
        result_text = data.get("result", raw_text)
        confidence = float(data.get("confidence", 0.8))
    except (json.JSONDecodeError, ValueError):
        result_text = raw_text
        confidence = 0.7

    return AgentResult(
        task_id=task.id,
        status=TaskStatus.completed,
        result=result_text,
        confidence=confidence,
        model_used=vlm_model,
        hardware="cpu",
        latency_ms=latency_ms,
        tool_calls=images_used,
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

    # For vision tasks, report the VLM model in task_started (not the CPU worker)
    # so the UI shows the correct model before the VLM call begins.
    if task.type == TaskType.vision:
        vlm_ep = os.getenv("VLM_ENDPOINT", "")
        started_model = os.getenv("VLM_MODEL", "microsoft/Phi-3.5-vision-instruct") if vlm_ep else client.model
        started_hw = "cpu"  # vllm-vision runs on CPU (OpenVINO) in this setup
    else:
        started_model = client.model
        started_hw = client.hardware

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.task_started,
            run_id=run_id,
            payload={
                "task_id": task.id,
                "description": task.description,
                "type": task.type.value,
                "model": started_model,
                "hardware": started_hw,
            },
        ),
    )

    # ── Vision tasks: separate code path ────────────────────────────────────
    if task.type == TaskType.vision:
        try:
            agent_result = await _execute_vision_task(task, run_id, broadcast, role_cfg)
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
            err = AgentResult(
                task_id=task.id,
                status=TaskStatus.failed,
                result=str(exc),
                confidence=0.0,
                model_used=os.getenv("VLM_MODEL", "vision-fallback"),
                hardware="cpu",
                latency_ms=0.0,
            )
            await broadcast(run_id, SwarmEvent(
                event=EventType.task_failed,
                run_id=run_id,
                payload={"task_id": task.id, "error": str(exc)},
            ))
            return err

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

    # Writing tasks get a larger token budget and store raw JSON for the reducer
    max_tokens = 2048 if task.type == TaskType.writing else 768

    try:
        raw, latency_ms = await client.complete(messages, max_tokens=max_tokens)
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
