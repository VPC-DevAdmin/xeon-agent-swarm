#!/usr/bin/env python
"""
scripts/mock_router.py — mock OpenAI-compatible tier-router gateway for OFFLINE
end-to-end testing of the real deepagents engine.

WHAT IT IS
    A stand-in for the external semantic tier router (normally :8900). It speaks
    the same wire contract as the real gateway (see docs/router-contract.md and
    backend/inference/model.py): POST /v1/chat/completions, model is a TIER
    SELECTOR ("auto" or "tier1".."tier5", never a model id), streaming rejected
    with 400, and every response carries the x-vsr-selected-* routing headers
    that backend/observability/callbacks.py + event_adapter.py read.

    Instead of calling any model it classifies each request (judge? planner?
    delegation loop? worker?) and returns deterministic canned responses that
    are smart enough to drive the REAL agent loop end to end:

      planner   -> submit_plan tool_call (when the HITL gate is on), then
                   sequential `task` delegations research -> analysis -> writing,
                   then a final synthesis message
      workers   -> role-appropriate canned envelopes that PASS validate_l0
                   (backend/observability/validation_l0.py)
      judges    -> the exact verdict JSON validation_judge.py parses (always pass)

WHY IT EXISTS
    *** Scale / soak tests must NEVER hit real cloud APIs. ***
    Point the backend at this mock and the full engine (deepagents graph, event
    adapter, validation stack, persistence, budgets) runs end-to-end with ZERO
    cloud API calls and zero token spend.

HOW TO USE
    # terminal 1 — start the mock (default port 8901)
    python scripts/mock_router.py

    # terminal 2 — point the engine at it, then run whatever you like
    export ROUTER_BASE=http://localhost:8901
    export ROUTER_BASE_URL=http://localhost:8901/v1

ENV KNOBS
    MOCK_ROUTER_PORT   listen port                       (default 8901)
    MOCK_LATENCY_MS    per-request artificial latency    (default 0)
    MOCK_CACHE_EVERY   simulate a gateway cache hit on every Nth request by
                       omitting the x-vsr-selected-model header (default 0 = never)
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import re
import uuid
import zlib
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="mock-tier-router")

_VALID_MODELS = {"auto", "tier1", "tier2", "tier3", "tier4", "tier5"}
_request_counter = itertools.count(1)

# ── message helpers ──────────────────────────────────────────────────────────

def _text(content) -> str:
    """Flatten str-or-content-parts message content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return "" if content is None else str(content)


def _system_text(messages: list[dict]) -> str:
    return " ".join(_text(m.get("content")) for m in messages
                    if m.get("role") in ("system", "developer"))


def _objective(messages: list[dict]) -> str:
    """The run objective is the first user message."""
    for m in messages:
        if m.get("role") == "user":
            return _text(m.get("content")).strip()
    return "the requested objective"


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _text(m.get("content"))
    return ""


def _tool_names(body: dict) -> set[str]:
    names = set()
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = fn.get("name")
        if name:
            names.add(name)
    return names


def _tool_result_count(messages: list[dict], tool_name: str) -> int:
    """Count role='tool' results that answer a prior assistant tool_call to `tool_name`."""
    ids = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if fn.get("name") == tool_name:
                ids.add(tc.get("id"))
    return sum(1 for m in messages
               if m.get("role") == "tool" and m.get("tool_call_id") in ids)

# ── deterministic routing headers ────────────────────────────────────────────

def _hash(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(), 16)


def _resolve_tier(model: str, seed: str) -> str:
    """Pinned tiers echo back; 'auto' picks tier1/2/3 deterministically."""
    if model != "auto":
        return model
    return f"tier{(_hash(seed) % 3) + 1}"


def _headers(tier: str, category: str, seed: str) -> dict[str, str]:
    n = next(_request_counter)
    cache_every = int(os.environ.get("MOCK_CACHE_EVERY", "0") or 0)
    hdrs = {
        "x-vsr-selected-category": category,
        "x-vsr-selected-confidence": f"{0.80 + (_hash(seed) % 15) / 100:.2f}",
    }
    if not (cache_every and n % cache_every == 0):
        # Absence of x-vsr-selected-model == cache hit (callbacks.py); the body
        # model field (already a tier id) is the fallback in that case.
        hdrs["x-vsr-selected-model"] = tier
    return hdrs

# ── canned content ───────────────────────────────────────────────────────────

_JUDGE_MARKER = "output validator"          # matches _GRADER_SYSTEM and _SYNTH_GRADER_SYSTEM
_PARTIAL_SYNTH_MARKER = "stopped early"     # _PARTIAL_SYNTH_SYSTEM (budget-stop fallback)

# Role detection: distinctive phrases from config/worker_roles.yaml system prompts.
_ROLE_MARKERS: list[tuple[str, str]] = [
    ("research", "research specialist"),
    ("analysis", "analysis specialist"),
    ("code", "code specialist"),
    ("vision", "technical vision analyst"),
    ("fact_check", "fact-checking specialist"),
    ("writing", "technical writing specialist"),
    ("summarization", "summarization specialist"),
    ("general-purpose", "general-purpose reasoning agent"),
]

_CATEGORY = {"research": "research", "writing": "writing"}

# Tool-echo: when a run enables tools, the planner's system prompt carries the
# manifest ("AVAILABLE TOOLS … - <id> (caps): …"). The mock then inserts a
# tool_user delegation and, for that worker, actually invokes the granted tool so
# the real StructuredTool executes offline (e.g. csv_file writes a file).
_TOOL_MANIFEST_MARKER = "available tools"
_TOOL_USER_MARKER = "tool-using agent"
_DEEPAGENTS_BUILTINS = {"task", "submit_plan", "write_todos", "read_file",
                        "write_file", "edit_file", "ls"}


def _enabled_tool(system_text: str) -> str | None:
    """First tool id from the planner manifest, or None when no tools are enabled."""
    if _TOOL_MANIFEST_MARKER not in system_text:
        return None
    m = re.search(r"^- (\w+) \(", system_text, re.MULTILINE)
    return m.group(1) if m else None


def _granted_tool(body: dict) -> str | None:
    """The first catalog tool granted to this (tool_user) subagent request."""
    for name in _tool_names(body):
        if name not in _DEEPAGENTS_BUILTINS:
            return name
    return None


def _tool_call_args(tool_id: str, obj: str) -> dict:
    """A safe, deterministic {action, params} for the given tool (impls tolerate
    unknown shapes and degrade to an error string, so any of these is safe)."""
    o = obj[:80]
    table = {
        "csv_file":     {"action": "append", "params": {"row": {"finding": o, "status": "recorded"}}},
        "sql_database": {"action": "query", "params": {"sql": "SELECT 1 AS ok"}},
        "telegram":     {"action": "send", "params": {"text": f"Digest ready: {o}"}},
        "sms":          {"action": "send", "params": {"to": "+10000000000", "body": f"Digest: {o}"}},
        "email":        {"action": "send", "params": {"to": "demo@example.com", "subject": "Digest", "body": o}},
        "webhook":      {"action": "post", "params": {"payload": {"summary": o}}},
        "x_twitter":    {"action": "read", "params": {"query": o}},
    }
    return table.get(tool_id, {"action": "read", "params": {}})


def _tool_results_present(messages: list[dict]) -> bool:
    """True once a non-delegation tool result (an actual tool call) is in the thread."""
    for m in messages:
        if m.get("role") == "tool" and m.get("name") not in ("task", "submit_plan"):
            return True
    return False

# The delegation script the mock planner follows (roles exist in worker_roles.yaml).
_SUBTASKS: list[tuple[str, str]] = [
    ("research", "Research the topic: {obj} — gather the key facts, specific "
                 "numbers, and sources."),
    ("analysis", "Analyze the research findings for: {obj} — compare the options, "
                 "quantify the tradeoffs, and produce a comparison table."),
    ("writing",  "Write the final brief for: {obj} — synthesize the research and "
                 "analysis findings into a structured report."),
]


_SECTION_RE = re.compile(
    r"### SECTION ([A-Z]) ###\n(.*?)(?=\n### SECTION [A-Z] ###|\Z)", re.S)
_ROLE_SECTION = {"research": 0, "analysis": 1, "writing": 2}


def _worker_slice(obj: str, role: str) -> str | None:
    """The worker's assigned retrieval section, when the objective carries a
    sectioned corpus (workload v15 realistic slicing: the planner reads the
    whole retrieval once, each worker carries only its slice)."""
    idx = _ROLE_SECTION.get(role)
    if idx is None:
        return None
    found = _SECTION_RE.findall(obj)
    return found[idx][1].strip() if idx < len(found) else None


def _plan_text(obj: str) -> str:
    return ("1. Research the topic: gather key facts, numbers, and sources for "
            f"'{obj[:120]}'.\n"
            "2. Analyze the findings: compare options, quantify tradeoffs, build "
            "a comparison table.\n"
            "3. Write the final brief synthesizing the research and analysis.")


def _worker_content(role: str, obj: str) -> str:
    """Canned worker output that passes validate_l0 for the role (no tool calls)."""
    if role == "research":
        return json.dumps({
            "result": (
                "Canned research findings for the subtask. Benchmark data shows the "
                "leading system sustains 2400 req/s at 200 concurrent requests versus "
                "100 req/s for the baseline, a 24x throughput advantage. Median latency "
                "is 38 ms against 95 ms, a 60% reduction, while unit cost falls from "
                "$0.42 to $0.11 per thousand requests (74% cheaper). Adoption grew 3.2x "
                "year over year across the surveyed deployments. Two independent sources "
                "corroborate the throughput figure within a 5% margin. These numbers are "
                "synthetic fixtures emitted by the mock router for offline scale testing."
            ),
            "confidence": 0.85,
            "artifact": {
                "type": "citation_set",
                "content": {"citations": [
                    {"title": "Mock benchmark report 2026",
                     "url": "https://example.com/mock-benchmark-2026",
                     "snippet": "2400 req/s vs 100 req/s at 200 concurrent requests (24x)."},
                    {"title": "Mock cost analysis",
                     "url": "https://example.com/mock-cost-analysis",
                     "snippet": "$0.11 vs $0.42 per 1k requests; median latency 38 ms vs 95 ms."},
                ]},
            },
        })
    if role == "analysis":
        return json.dumps({
            "result": ("System A leads on throughput by 24x (2400 vs 100 req/s) and cost "
                       "by 74% ($0.11 vs $0.42 per 1k requests); System B's only win is "
                       "simplicity of deployment."),
            "confidence": 0.82,
            "artifacts": [
                {"type": "table", "content": {
                    "caption": "Canned comparison of the two options",
                    "headers": ["System", "Throughput (req/s)", "Latency (ms)",
                                "Cost ($/1k req)", "Winner"],
                    "rows": [["System A", "2400", "38", "0.11", "yes"],
                             ["System B", "100", "95", "0.42", ""]],
                }},
                {"type": "chart", "content": {
                    "caption": "Throughput at 200 concurrent requests",
                    "chart_type": "bar", "x_label": "System", "y_label": "req/s",
                    "series": [{"name": "Throughput",
                                "data": [{"x": "System A", "y": 2400},
                                         {"x": "System B", "y": 100}]}],
                }},
            ],
        })
    if role == "code":
        return json.dumps({
            "result": ("A minimal async pipeline snippet illustrating the pattern; "
                       "canned output from the mock router."),
            "confidence": 0.8,
            "artifacts": [
                {"type": "code", "content": {
                    "language": "python",
                    "description": "Minimal async worker pipeline (mock fixture)",
                    "code": ("import asyncio\n\n\n"
                             "async def worker(n: int) -> int:\n"
                             "    await asyncio.sleep(0)\n"
                             "    return n * 2\n\n\n"
                             "async def main() -> None:\n"
                             "    results = await asyncio.gather(*(worker(i) for i in range(4)))\n"
                             "    print(results)\n\n\n"
                             "asyncio.run(main())\n"),
                }},
                {"type": "diagram", "content": {
                    "mermaid": "graph TD\n  A[Input] --> B[Workers] --> C[Gather] --> D[Output]",
                    "caption": "Architecture: fan-out workers gathered into one result",
                }},
            ],
        })
    if role == "vision":
        return json.dumps({
            "result": ("Extracted from the chart (canned): System A bar reads 2400 req/s, "
                       "System B bar reads 100 req/s at the 200-concurrent-requests tick; "
                       "System A wins by 24x."),
            "confidence": 0.8,
            "images_analyzed": 1,
            "artifact": {"type": "extracted_data", "content": {
                "description": "Bar values read from a mock throughput chart",
                "data_points": [
                    {"label": "System A at 200 req", "value": "2400", "unit": "req/s"},
                    {"label": "System B at 200 req", "value": "100", "unit": "req/s"},
                ],
                "source_image": "",
            }},
        })
    if role == "fact_check":
        return json.dumps({
            "result": "2 supported, 0 unsupported, 1 uncertain",
            "confidence": 0.8,
            "artifacts": [
                {"type": "claim_verdict", "content": {
                    "claim": "Throughput advantage is 24x at 200 concurrent requests",
                    "verdict": "supported",
                    "evidence": "Mock benchmark report: 2400 req/s vs 100 req/s.",
                }},
                {"type": "claim_verdict", "content": {
                    "claim": "Unit cost is 74% lower",
                    "verdict": "supported",
                    "evidence": "Mock cost analysis: $0.11 vs $0.42 per 1k requests.",
                }},
            ],
        })
    if role == "summarization":
        return json.dumps({
            "result": ("Canned summary: the leading option is 24x faster (2400 vs 100 "
                       "req/s), 60% lower latency, and 74% cheaper per request; the "
                       "baseline's only advantage is simpler deployment."),
            "key_points": ["24x throughput advantage", "60% latency reduction",
                           "74% lower unit cost"],
            "confidence": 0.8,
        })
    if role == "writing":
        # 'writing' is not a JSON-envelope role in validation_l0 — prose is on-contract
        # for L0 (non-empty + length). Canned brief, plainly marked as a fixture.
        return (
            f"Final Brief (mock fixture): {obj[:160]}\n\n"
            "Executive summary. Across every quantitative dimension measured in the "
            "canned research, the leading system dominates: it sustains 2400 req/s at "
            "200 concurrent requests against the baseline's 100 req/s — a 24x throughput "
            "advantage — while cutting median latency from 95 ms to 38 ms (a 60% "
            "reduction) and lowering unit cost from $0.42 to $0.11 per thousand requests "
            "(74% cheaper).\n\n"
            "Analysis. The comparison table produced upstream ranks System A first on "
            "throughput, latency, and cost; System B's only advantage is deployment "
            "simplicity. Adoption grew 3.2x year over year in the surveyed deployments, "
            "and two independent sources corroborate the throughput figure within a 5% "
            "margin.\n\n"
            "Recommendation. Adopt System A for any workload where throughput or unit "
            "economics matter; reserve System B for low-traffic environments where "
            "operational simplicity outweighs a 24x performance gap. These findings are "
            "deterministic fixtures emitted by the mock router for offline end-to-end "
            "scale testing — no real model produced them."
        )
    # general-purpose / unknown role fallback: minimal envelope that passes L0.
    return json.dumps({
        "result": ("Canned general-purpose answer for the subtask, produced by the mock "
                   "router: the key figure to carry forward is a 24x throughput advantage "
                   "(2400 vs 100 req/s) at 200 concurrent requests."),
        "confidence": 0.8,
    })


def _synthesis_text(obj: str) -> str:
    """Final main-agent answer composed from the canned findings (~200 words)."""
    return (
        f"Answer (mock synthesis) for: {obj[:160]}\n\n"
        "Drawing the three subtask results together, the picture is consistent. The "
        "research worker found that the leading system sustains 2400 req/s at 200 "
        "concurrent requests versus 100 req/s for the baseline — a 24x throughput "
        "advantage — with median latency of 38 ms against 95 ms (a 60% reduction) and "
        "unit cost of $0.11 versus $0.42 per thousand requests (74% cheaper). The "
        "analysis worker's comparison table confirms the same ranking on every metric "
        "that matters: throughput, latency, and cost all favor System A, with System B "
        "retaining only a deployment-simplicity edge. The writing worker's brief folds "
        "these figures into a clear recommendation: adopt System A wherever throughput "
        "or unit economics are decisive, and reserve System B for low-traffic settings "
        "where operational simplicity outweighs a 24x performance gap. Adoption trends "
        "(3.2x year-over-year growth) and two independently corroborating sources "
        "within a 5% margin reinforce confidence in the numbers. Assumption stated "
        "up front: this run executed against the mock router, so every figure above is "
        "a deterministic offline test fixture rather than a real-world measurement."
    )

# ── response assembly ────────────────────────────────────────────────────────

def _usage(messages: list[dict], completion_text: str) -> dict:
    prompt_tokens = max(1, sum(len(_text(m.get("content"))) for m in messages) // 4)
    completion_tokens = max(1, len(completion_text) // 4)
    return {"prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens}


def _serving_profile() -> dict | None:
    """The modeled serving tier, or None for the zero-latency stand-in.

    Per-call wait = TTFT + output_tokens/decode + input_tokens/prefill,
    computed from the ACTUAL request and response payloads, so a heavy
    researcher planner call waits several times longer than a judge
    verdict without any per-role table. Three numbers define the tier and
    ride the run's provenance; seeded jitter (+/-20%) decoheres arrivals."""
    try:
        ttft = float(os.environ.get("CAPACITY_MODEL_TTFT_MS", "0") or 0)
        decode = float(os.environ.get("CAPACITY_MODEL_DECODE_TPS", "0") or 0)
        prefill = float(os.environ.get("CAPACITY_MODEL_PREFILL_TPS", "0") or 0)
    except ValueError:
        return None
    if decode <= 0 and prefill <= 0 and ttft <= 0:
        return None
    return {"ttft_ms": ttft, "decode_tps": decode, "prefill_tps": prefill}


def _model_wait_s(messages: list[dict], completion_text: str, seed: str) -> float:
    prof = _serving_profile()
    if not prof:
        return 0.0
    tokens_in = sum(len(_text(m.get("content"))) for m in messages) / 4.0
    tokens_out = max(1.0, len(completion_text) / 4.0)
    wait = prof["ttft_ms"] / 1000.0
    if prof["decode_tps"] > 0:
        wait += tokens_out / prof["decode_tps"]
    if prof["prefill_tps"] > 0:
        wait += tokens_in / prof["prefill_tps"]
    jitter = 0.8 + 0.4 * ((_hash(seed + completion_text[:40]) % 1000) / 1000.0)
    return wait * jitter


async def _completion(*, tier: str, category: str, seed: str, messages: list[dict],
                content: str | None = None, tool_calls: list[dict] | None = None) -> JSONResponse:
    message: dict = {"role": "assistant", "content": content}
    finish_reason = "stop"
    completion_text = content or ""
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
        completion_text = json.dumps(tool_calls)
    wait = _model_wait_s(messages, completion_text, seed)
    if wait > 0:
        await asyncio.sleep(wait)
    body = {
        "id": f"chatcmpl-mock-{int(time.time() * 1000)}-{_hash(seed) % 10_000}",
        "object": "chat.completion",
        "created": int(time.time()),
        # The real gateway's body model field is already a tier id (model.py).
        "model": tier,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": _usage(messages, completion_text),
    }
    return JSONResponse(body, headers=_headers(tier, category, seed))


def _error(status: int, message: str, param: str | None = None,
           code: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error",
                   "param": param, "code": code}},
        status_code=status,
    )


_tool_call_counter = itertools.count(1)


def _tool_call(name: str, args: dict) -> dict:
    # Unique across the mock's worker PROCESSES, not just within one: a
    # per-process counter handed two planner turns of the same run the
    # same id when different processes served them, and the orchestrator's
    # stream adapter (keyed by tool-call id) then overwrote the first
    # worker's record - the ~0.2% "dropped tool call" defect.
    return {"id": f"call_mock_{uuid.uuid4().hex[:20]}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}

# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "mock": True}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Malformed JSON request body.")
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return _error(400, "Request must include a 'messages' array.", param="messages")

    if body.get("stream"):
        return _error(400, "Streaming is not supported by this gateway.", param="stream")

    model = body.get("model")
    if model not in _VALID_MODELS:
        return _error(
            400,
            f"Invalid model {model!r}: the model field is a tier selector — "
            "use 'auto' or 'tier1'..'tier5', never a model id.",
            param="model",
        )

    latency_ms = int(os.environ.get("MOCK_LATENCY_MS", "0") or 0)
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)

    messages = body["messages"]
    system = _system_text(messages).lower()
    tools = _tool_names(body)
    obj = _objective(messages)
    seed = _last_user_text(messages) or obj
    tier = _resolve_tier(model, seed)

    # (a) judge / grader call — answer with the verdict JSON validation_judge parses.
    if _JUDGE_MARKER in system:
        content = json.dumps({"verdict": "pass", "score": 0.92, "critique": ""})
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, content=content)

    # (b) plan-approval gate: submit_plan available and not yet answered.
    if "submit_plan" in tools and _tool_result_count(messages, "submit_plan") == 0:
        tc = _tool_call("submit_plan", {"plan": _plan_text(obj)})
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, tool_calls=[tc])

    # (c) main agent: sequential delegation loop, then synthesis. When tools are
    # enabled (manifest present), splice in a tool_user delegation before writing.
    if "task" in tools:
        script = list(_SUBTASKS)
        tool_id = _enabled_tool(system)
        if tool_id:
            script.insert(2, ("tool_user",
                              f"Use {tool_id} to record and act on the findings for: {{obj}}"))
        done = _tool_result_count(messages, "task")
        if done < len(script):
            role, desc_tpl = script[done]
            desc = desc_tpl.format(obj=obj[:300])
            piece = _worker_slice(obj, role)
            if piece:
                desc += "\n\nUse ONLY this retrieved context:\n" + piece
            tc = _tool_call("task", {"subagent_type": role,
                                     "description": desc})
            return await _completion(tier=tier, category="general", seed=seed,
                               messages=messages, tool_calls=[tc])
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, content=_synthesis_text(obj))

    # (c2) tool_user subagent: actually invoke the granted tool, then report its result.
    if _TOOL_USER_MARKER in system:
        tool_id = _granted_tool(body)
        if tool_id and not _tool_results_present(messages):
            tc = _tool_call(tool_id, _tool_call_args(tool_id, obj))
            return await _completion(tier=tier, category="general", seed=seed,
                               messages=messages, tool_calls=[tc])
        # tool has run (its result is in the thread) — report it on-contract for L0.
        content = json.dumps({
            "result": (f"Called {tool_id or 'the tool'}; it executed and returned its result "
                       "(see the tool output above)."),
            "confidence": 0.8,
        })
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, content=content)

    # Budget-stop partial synthesis (no tools, distinctive system prompt): prose answer.
    if _PARTIAL_SYNTH_MARKER in system:
        return await _completion(tier=tier, category="writing", seed=seed,
                           messages=messages, content=_synthesis_text(obj))

    # (d0) benchmark tool exercise, in sequence: a worker granted
    # bench_retrieve performs real on-box retrieval FIRST (workload v16 -
    # the retrieved chunks become its working context), then the durable
    # bench_record call, then its answer. A worker granted only
    # bench_record keeps the v14/v15 single-call shape.
    # Retrieval policy by archetype: the researcher retrieves in every
    # worker; the comparison (medium) retrieves in its research worker only.
    _role_now = next((r for r, marker in _ROLE_MARKERS if marker in system),
                     "general-purpose")
    _wants_retrieval = ("bench_retrieve" in tools and (
        "Using ONLY the measurements" not in obj or _role_now == "research"))
    if _wants_retrieval and _tool_result_count(messages, "bench_retrieve") == 0:
        topic = zlib.crc32(f"{obj[:80]}|{seed}".encode()) % 2000
        tc = _tool_call("bench_retrieve",
                        {"query": f"topic{topic} " + " ".join(
                            re.findall(r"[a-z]+", obj.lower())[:6])})
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, tool_calls=[tc])
    if ("bench_record" in tools
            and _tool_result_count(messages, "bench_record") == 0):
        tc = _tool_call("bench_record", {"key": (obj or "record")[:40]})
        return await _completion(tier=tier, category="general", seed=seed,
                           messages=messages, tool_calls=[tc])

    # (d) worker subagent: detect role from the system prompt, return canned content
    # that passes validate_l0. Workers never emit tool calls.
    role = next((r for r, marker in _ROLE_MARKERS if marker in system), "general-purpose")
    content = _worker_content(role, obj)
    # Grounded by construction: a worker that retrieved cites the chunk ids
    # it was actually given, so a host-side grounding check has something
    # real to verify (citations must be a subset of the retrieved set).
    cited: list[str] = []
    for m in messages:
        if m.get("role") == "tool":
            ids = re.findall(r"\[chunk-(\d+)\]", _text(m.get("content")))
            if ids:
                cited = ids[:4]
    if cited:
        src = " Sources: " + " ".join(f"[chunk-{c}]" for c in cited) + "."
        try:
            doc = json.loads(content)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and isinstance(doc.get("result"), str):
            doc["result"] += src
            content = json.dumps(doc)
        else:
            content += src
    return await _completion(tier=tier, category=_CATEGORY.get(role, "general"), seed=seed,
                       messages=messages, content=content)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MOCK_ROUTER_PORT", "8901"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
