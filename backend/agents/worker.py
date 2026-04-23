"""
Generic worker agent: executes a single TaskSpec and returns an AgentResult.
Role (system prompt + tools) is loaded from config/worker_roles.yaml.

Single-engine topology: all text workers share one Mistral-7B engine (TEXT_ENGINE_ENDPOINT).
Vision uses a separate VLM endpoint. Specialization comes from contracts + prompts, not models.

Optional validator retry loop:
  If state.validator_enabled is True, each worker result is checked against the TaskSpec
  contract. Failed checks trigger a retry with a correction_hint injected into the prompt.
  Retries are capped per role (max_retries in worker_roles.yaml, default 3).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

from openai import AsyncOpenAI

from backend.corpus.embedder import Embedder
from backend.corpus.redis_imagestore import RedisImageStore
from backend.corpus.seed_data import CORPORA
from backend.inference.client import InferenceClient
from backend.schemas.models import (
    Artifact,
    ArtifactType,
    AgentResult,
    SwarmState,
    TaskSpec,
    TaskStatus,
    TaskType,
    EventType,
    SwarmEvent,
    ValidationVerdict,
    validate_code_syntax,
)
from backend.protocols.mcp_servers import call_tool

_IMAGE_DIR = Path(os.getenv("IMAGE_DIR", "/data/images"))

_ROLES: dict | None = None


def _load_roles() -> dict:
    global _ROLES
    if _ROLES is None:
        cfg_path = Path(os.getenv("CONFIG_DIR", "/app/config")) / "worker_roles.yaml"
        if not cfg_path.exists():
            cfg_path = Path(__file__).parent.parent.parent / "config" / "worker_roles.yaml"
        with open(cfg_path) as f:
            _ROLES = yaml.safe_load(f)["roles"]
    return _ROLES


def _client_for_role(task_type: TaskType) -> InferenceClient:
    """
    Single-engine topology: all text workers share one Mistral-7B engine.
    Vision uses its own endpoint. Specialization comes from contracts, not models.

    Role            Engine                        Rationale
    ─────────────   ──────────────────────────    ───────────────────────────────
    vision          VLM_ENDPOINT                  Separate — modality requires different model
    all others      TEXT_ENGINE_ENDPOINT           Shared Mistral-7B with concurrency semaphore
    """
    if task_type == TaskType.vision:
        return InferenceClient(
            base_url=os.getenv("VLM_ENDPOINT", "http://localhost:8084/v1"),
            model=os.getenv("VLM_MODEL", "microsoft/Phi-3.5-vision-instruct"),
            hardware="cpu",
            use_semaphore=False,  # vision has its own engine, no text engine contention
        )

    # All text tasks: shared Mistral-7B with semaphore
    return InferenceClient(
        base_url=os.getenv(
            "TEXT_ENGINE_ENDPOINT",
            os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1"),
        ),
        model=os.getenv(
            "TEXT_ENGINE_MODEL",
            os.getenv("ORCHESTRATOR_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        ),
        hardware="cpu",
        use_semaphore=True,
    )


def _extract_artifacts(data: dict, task_id: str) -> list[Artifact]:
    """
    Pull typed artifacts out of a worker's JSON response.

    Workers may return:
      - "artifact": {...}          — single artifact
      - "artifacts": [{...}, ...]  — multiple artifacts (code worker returns 2)

    Code artifacts get server-side Python syntax validation.
    Vision artifacts get source_image populated from tool_calls if present.
    """
    raw_artifacts: list[dict] = []
    if "artifact" in data and data["artifact"]:
        raw_artifacts = [data["artifact"]]
    elif "artifacts" in data and data["artifacts"]:
        raw_artifacts = [a for a in data["artifacts"] if a]

    artifacts: list[Artifact] = []
    for raw in raw_artifacts:
        try:
            art_type = ArtifactType(raw.get("type", "prose"))
            content: dict = raw.get("content", {})

            # Validate Python syntax for code artifacts
            if art_type == ArtifactType.code:
                code_str = content.get("code", "")
                lang = content.get("language", "")
                content["syntax_valid"] = validate_code_syntax(code_str, lang)

            artifacts.append(
                Artifact(
                    type=art_type,
                    content=content,
                    worker_id=task_id,
                    confidence=float(data.get("confidence", 0.8)),
                )
            )
        except Exception as exc:
            logger.debug("Artifact parse error task=%s: %s | raw=%s", task_id, exc, raw)

    return artifacts


def _parse_worker_response(raw: str, task_id: str, client: InferenceClient, latency_ms: float) -> AgentResult:
    """Parse the model's JSON response; extract typed artifacts; fall back to plain text."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    try:
        data = json.loads(text)
        result = data.get("result", raw)
        confidence = float(data.get("confidence", 0.8))
        artifacts = _extract_artifacts(data, task_id)
    except (json.JSONDecodeError, ValueError):
        result = raw
        confidence = 0.5
        artifacts = []

    return AgentResult(
        task_id=task_id,
        status=TaskStatus.completed,
        result=result,
        artifacts=artifacts,
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

    all_hits.sort(key=lambda h: h["score"])
    return all_hits[:top_k]


async def _execute_text_retrieval_fallback(
    task: TaskSpec,
    role_cfg: dict,
    correction_hint: str | None = None,
) -> AgentResult:
    """
    Text-only fallback when no images are found for a vision task.
    Uses the shared text engine with the research role prompt.
    """
    client = InferenceClient(
        base_url=os.getenv(
            "TEXT_ENGINE_ENDPOINT",
            os.getenv("ORCHESTRATOR_ENDPOINT", "http://localhost:8080/v1"),
        ),
        model=os.getenv(
            "TEXT_ENGINE_MODEL",
            os.getenv("ORCHESTRATOR_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        ),
        hardware="cpu",
        use_semaphore=True,
    )
    task_desc = task.objective or task.description
    user_content = (
        f"Vision task fallback (no images found in corpus).\n"
        f"Use text-based retrieval instead.\n\n"
        f"Original objective: {task_desc}\n"
        f"Scope: {'; '.join(task.scope) if task.scope else 'not specified'}"
    )
    if correction_hint:
        user_content += f"\n\nCorrection hint from previous attempt: {correction_hint}"

    messages = [
        {"role": "system", "content": role_cfg["system_prompt"]},
        {"role": "user", "content": user_content},
    ]
    raw, latency_ms = await client.complete(messages, max_tokens=512)
    result = _parse_worker_response(raw, task.id, client, latency_ms)
    result.tool_calls = ["fallback:no_images_text_retrieval"]
    return result


async def _execute_vision_task(
    task: TaskSpec,
    run_id: str,
    broadcast,
    role_cfg: dict,
    correction_hint: str | None = None,
) -> AgentResult:
    """
    Vision worker: retrieve relevant images → VLM call with base64 images.

    Honors task.fallback_behavior when no images are found:
      "skip"           → return VisionResult with image_found=False, confidence=0
      "retrieval_only" → fall back to text-based retrieval (default)
      "describe"       → call VLM anyway (no image, just text prompt)
    """
    t0 = time.perf_counter()
    vlm_endpoint = os.getenv("VLM_ENDPOINT", "")
    vlm_model = os.getenv("VLM_MODEL", "microsoft/Phi-3.5-vision-instruct")

    task_desc = task.objective or task.description
    hits = await _retrieve_images(task_desc, top_k=1)

    # ── No images found — honor fallback_behavior ────────────────────────────
    if not hits:
        fallback = task.fallback_behavior
        logger.info("Vision task %s: no images found; fallback=%s", task.id, fallback)

        if fallback == "skip":
            latency_ms = (time.perf_counter() - t0) * 1000
            return AgentResult(
                task_id=task.id,
                status=TaskStatus.completed,
                result="No relevant image in corpus; task skipped per fallback_behavior=skip",
                artifacts=[
                    Artifact(
                        type=ArtifactType.extracted_data,
                        content={
                            "description": "No image found",
                            "image_found": False,
                            "data_points": [],
                        },
                        worker_id=task.id,
                        confidence=0.0,
                    )
                ],
                confidence=0.0,
                model_used="vision_skipped",
                hardware="n/a",
                latency_ms=latency_ms,
            )

        if fallback == "retrieval_only":
            return await _execute_text_retrieval_fallback(task, role_cfg, correction_hint)

        # fallback == "describe": fall through to VLM call (text only, no image)

    # ── VLM not configured ───────────────────────────────────────────────────
    if not vlm_endpoint:
        img_count = len(hits)
        img_note = (
            f"{img_count} relevant image(s) were retrieved from the corpus "
            f"but could not be analyzed because the vision worker (VLM_ENDPOINT) "
            f"is not running. Start vllm-vision with: "
            f"docker compose --profile vision up -d vllm-vision"
            if img_count > 0
            else "No images matched the query and the vision worker (VLM_ENDPOINT) is not running."
        )
        # Fall back to text retrieval
        return await _execute_text_retrieval_fallback(task, role_cfg, correction_hint)

    # ── Build VLM message content ────────────────────────────────────────────
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

    # Build type-specific extraction directive from expected_image_types
    extraction_directives = ""
    if task.expected_image_types:
        if "benchmark_chart" in task.expected_image_types:
            extraction_directives += (
                "\nFor benchmark charts: populate extracted_data with "
                "{data_points: [{label, value, unit}], axis_x, axis_y, title}"
            )
        if "architecture_diagram" in task.expected_image_types:
            extraction_directives += (
                "\nFor architecture diagrams: populate extracted_data with "
                "{components: [name], connections: [[from, to]], labeled_values: {}}"
            )

    extraction_prompt = (
        f"Task: {task_desc}\n\n"
        "IMPORTANT — do not describe these images at a high level. Read and extract "
        "the specific information they contain:\n"
        "• Charts/graphs: report exact axis labels, series names, and key numeric values\n"
        "• Architecture diagrams: list every labeled component and the connections between them\n"
        "• Tables: extract rows, columns, and their values\n"
        "• Pipeline diagrams: list each stage in order, noting parallel vs sequential steps"
        + extraction_directives
    )
    if correction_hint:
        extraction_prompt += f"\n\nCorrection hint from previous attempt: {correction_hint}"

    content.append({"type": "text", "text": extraction_prompt})

    vlm_client = AsyncOpenAI(base_url=vlm_endpoint, api_key="none")
    messages = [
        {"role": "system", "content": role_cfg["system_prompt"]},
        {"role": "user", "content": content},
    ]

    resp = await vlm_client.chat.completions.create(
        model=vlm_model,
        messages=messages,
        max_tokens=800,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    raw_text = (resp.choices[0].message.content or "").strip()

    if not raw_text:
        finish = resp.choices[0].finish_reason if resp.choices else "unknown"
        logger.warning(
            "VLM returned empty content (finish_reason=%s). images_used=%s",
            finish, images_used,
        )
        return AgentResult(
            task_id=task.id,
            status=TaskStatus.completed,
            result=(
                f"Vision model returned no output (finish_reason={finish!r}). "
                f"Image context likely exceeded max-model-len 4096. "
                f"Image retrieved: {images_used[0] if images_used else 'none'}"
            ),
            confidence=0.0,
            model_used=vlm_model,
            hardware="cpu",
            latency_ms=latency_ms,
            tool_calls=images_used,
        )

    try:
        data = json.loads(raw_text)
        result_text = data.get("result", raw_text)
        confidence = float(data.get("confidence", 0.8))
        artifacts = _extract_artifacts(data, task.id)
        for art in artifacts:
            if art.type == ArtifactType.extracted_data and images_used:
                art.content.setdefault("source_image", images_used[0])
    except (json.JSONDecodeError, ValueError):
        result_text = raw_text
        confidence = 0.7
        artifacts = []

    return AgentResult(
        task_id=task.id,
        status=TaskStatus.completed,
        result=result_text,
        artifacts=artifacts,
        confidence=confidence,
        model_used=vlm_model,
        hardware="cpu",
        latency_ms=latency_ms,
        tool_calls=images_used,
    )


# Per-role token budgets
_BUDGETS = {
    TaskType.writing:    2000,
    TaskType.research:   1200,
    TaskType.analysis:   1000,
    TaskType.fact_check:  400,
}


async def execute_task(
    task: TaskSpec,
    run_id: str,
    broadcast,
    context: dict[str, str] | None = None,
    correction_hint: str | None = None,
) -> AgentResult:
    """Execute a single task, broadcast events, return AgentResult."""
    roles = _load_roles()
    role_cfg = roles.get(task.type.value, roles["general"])
    client = _client_for_role(task.type)

    if task.type == TaskType.vision:
        vlm_ep = os.getenv("VLM_ENDPOINT", "")
        started_model = os.getenv("VLM_MODEL", "microsoft/Phi-3.5-vision-instruct") if vlm_ep else client.model
        started_hw = "cpu"
    else:
        started_model = client.model
        started_hw = client.hardware

    task_desc = task.objective or task.description

    await broadcast(
        run_id,
        SwarmEvent(
            event=EventType.task_started,
            run_id=run_id,
            payload={
                "task_id": task.id,
                "description": task_desc,
                "type": task.type.value,
                "model": started_model,
                "hardware": started_hw,
            },
        ),
    )

    # ── Vision tasks: separate code path ────────────────────────────────────
    if task.type == TaskType.vision:
        try:
            agent_result = await _execute_vision_task(task, run_id, broadcast, role_cfg, correction_hint)
            await broadcast(
                run_id,
                SwarmEvent(
                    event=EventType.task_completed,
                    run_id=run_id,
                    payload={
                        "task_id": task.id,
                        "result": agent_result.result,
                        "artifacts": [a.model_dump() for a in agent_result.artifacts],
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

    # ── MCP tool calls ───────────────────────────────────────────────────────
    tool_context = ""
    tool_calls_made: list[str] = []
    for tool_name in role_cfg.get("tools", []):
        tool_result = await call_tool(tool_name, {"query": task_desc})
        if tool_result:
            tool_context += f"\n\n[{tool_name} results]\n{tool_result}"
            tool_calls_made.append(tool_name)

    # Build user content — include scope and success_criteria for richer context
    user_content = task_desc
    if task.scope:
        user_content += "\n\nSpecific questions to answer:\n" + "\n".join(
            f"- {q}" for q in task.scope
        )
    if task.success_criteria:
        user_content += "\n\nSuccess criteria:\n" + "\n".join(
            f"- {c}" for c in task.success_criteria
        )
    if tool_context:
        user_content += f"\n\nAdditional context from tools:{tool_context}"
    if context:
        deps_text = "\n".join(f"- {k}: {v}" for k, v in context.items())
        user_content += f"\n\nResults from prerequisite tasks:\n{deps_text}"
    if correction_hint:
        user_content += f"\n\n[RETRY HINT] Previous attempt failed validation. Fix: {correction_hint}"

    messages = [
        {"role": "system", "content": role_cfg["system_prompt"]},
        {"role": "user", "content": user_content},
    ]

    max_tokens = _BUDGETS.get(task.type, 768)

    try:
        if task.type == TaskType.writing:
            # Stream tokens for live UI feedback
            accumulated = ""
            t0_stream = time.perf_counter()
            async for token in client.stream(messages, max_tokens=max_tokens):
                accumulated += token
                await broadcast(
                    run_id,
                    SwarmEvent(
                        event=EventType.task_token,
                        run_id=run_id,
                        payload={"task_id": task.id, "token": token},
                    ),
                )
            latency_ms = (time.perf_counter() - t0_stream) * 1000
            agent_result = _parse_worker_response(accumulated, task.id, client, latency_ms)
        else:
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
                    "artifacts": [a.model_dump() for a in agent_result.artifacts],
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


async def execute_task_with_validation(
    task: TaskSpec,
    run_id: str,
    broadcast,
    context: dict[str, str] | None = None,
    validator_enabled: bool = True,
) -> AgentResult:
    """
    Execute a task with optional validator retry loop.

    If validator_enabled:
      - Run the task
      - Check output against contract via validator
      - On failure: retry with correction_hint injected into the prompt
      - Retry budget: role's max_retries from worker_roles.yaml (default 3)
      - On exhausted retries: commit the last result with a warning

    Returns the final AgentResult regardless of validation status.
    """
    from backend.agents.validator import validate_worker_output

    roles = _load_roles()
    role_cfg = roles.get(task.type.value, roles["general"])
    max_retries = role_cfg.get("max_retries", 3)

    correction_hint: str | None = None
    last_result: AgentResult | None = None

    for attempt in range(1, max_retries + 2):  # +1 for initial attempt
        # Execute the task (with correction hint on retries)
        result = await execute_task(
            task=task,
            run_id=run_id,
            broadcast=broadcast,
            context=context,
            correction_hint=correction_hint,
        )
        last_result = result

        # Skip validation if disabled
        if not validator_enabled:
            return result

        # Skip validation for failed tasks
        if result.status != TaskStatus.completed:
            return result

        # Broadcast validator starting
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.validator_started,
                run_id=run_id,
                payload={"task_id": task.id, "attempt": attempt},
            ),
        )

        verdict: ValidationVerdict = await validate_worker_output(task, result)

        if verdict.compliant:
            await broadcast(
                run_id,
                SwarmEvent(
                    event=EventType.validator_approved,
                    run_id=run_id,
                    payload={"task_id": task.id, "attempt": attempt},
                ),
            )
            return result

        # Validation failed
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.validator_rejected,
                run_id=run_id,
                payload={
                    "task_id": task.id,
                    "attempt": attempt,
                    "failed_criteria": verdict.failed_criteria,
                    "correction_hint": verdict.correction_hint,
                    "severity": verdict.severity,
                },
            ),
        )

        if verdict.severity == "unfixable":
            logger.warning("Task %s: validator says unfixable — committing as-is", task.id)
            await broadcast(
                run_id,
                SwarmEvent(
                    event=EventType.worker_rejected_final,
                    run_id=run_id,
                    payload={"task_id": task.id, "reason": "unfixable"},
                ),
            )
            return result

        if attempt > max_retries:
            logger.warning(
                "Task %s: validator retry budget exhausted (%d retries) — committing as-is",
                task.id, max_retries,
            )
            await broadcast(
                run_id,
                SwarmEvent(
                    event=EventType.worker_rejected_final,
                    run_id=run_id,
                    payload={"task_id": task.id, "reason": "retries_exhausted"},
                ),
            )
            return result

        # Set up retry
        correction_hint = verdict.correction_hint
        logger.info(
            "Task %s: retrying (attempt %d/%d) hint=%r",
            task.id, attempt + 1, max_retries + 1, correction_hint[:80] if correction_hint else "",
        )
        await broadcast(
            run_id,
            SwarmEvent(
                event=EventType.worker_retrying,
                run_id=run_id,
                payload={
                    "task_id": task.id,
                    "next_attempt": attempt + 1,
                    "correction_hint": correction_hint,
                },
            ),
        )

    return last_result  # should not be reached


async def run_worker(inputs: dict) -> dict:
    """LangGraph node wrapper."""
    task: TaskSpec = inputs["task"]
    state: SwarmState = inputs["state"]

    async def _noop(run_id, event):
        pass

    validator_enabled = getattr(state, "validator_enabled", True)
    result = await execute_task_with_validation(
        task=task,
        run_id=state.run_id,
        broadcast=_noop,
        validator_enabled=validator_enabled,
    )
    state.results[task.id] = result
    return {"results": state.results}
