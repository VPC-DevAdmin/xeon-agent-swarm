# LLM Router Contract

**Status:** Draft v1 · Owner: agent-orchestration ↔ llm-router teams · Last updated: 2026-04-24

This document specifies the contract between this **agent-orchestration platform** and the **LLM router** that fronts all model serving. The orchestrator, validator, workers, and reducer are clients of the router; none of them serve LLMs in-process.

The contract is intentionally close to the standard OpenAI Chat Completions API, with three router-specific response headers for traceability and a single addition (`metadata`) on the request. Anything beyond that is either (a) OpenAI-spec-standard or (b) an explicit commitment from the router team — listed in [Section 6](#6-commitments-from-the-router-team).

---

## 1. Endpoint and wire format

### 1.1 Endpoint

```
POST {LLM_TIER_ENDPOINT}/v1/chat/completions
```

`LLM_TIER_ENDPOINT` is set per environment in the orchestrator's `.env`. Example: `https://router.internal/v1`.

### 1.2 Authentication

Standard `Authorization: Bearer <token>` header. Tokens are issued per-environment and rotated by the router team. The orchestrator stores the active token in `LLM_TIER_TOKEN` env var. No OAuth flow is required for v1.

### 1.3 Wire format

Pure OpenAI Chat Completions API. All request and response bodies match the OpenAI specification exactly, with the small additions called out in [Section 3](#3-request-shape) and [Section 4](#4-response-shape). Clients can use any standard OpenAI SDK against this endpoint.

---

## 2. Specialty model names

Clients pass a specialty name in the `model` field instead of a raw model ID. The router resolves the specialty to a concrete model. Specialties are **versioned** and **strictly pinned**.

### 2.1 Initial specialties

| Specialty | Purpose | Expected behavior |
|---|---|---|
| `orchestrator-v2.1` | Prompt decomposition into task graphs | Strong structured-output adherence; modest context; ~2k–4k completion tokens |
| `validator-v1.0` | Evaluate worker output against `success_criteria` | Fast LLM-as-judge; small completion (≤300 tokens); high fanout |
| `worker-default-v1.0` | General-purpose worker (research, analysis, summarization, writing) | Largest context window of any specialty; up to ~6k completion tokens |
| `worker-code-v1.0` | Code generation worker | Code-focused base model; AST-valid output expected |
| `worker-vision-v1.0` | Multimodal worker for chart/diagram extraction | Image input via standard OpenAI vision content parts |

Each name encodes both **role** and **version**. The router team owns model selection within a version. The orchestrator team owns when to bump.

### 2.2 Versioning rules

- A specialty name **never silently changes** the underlying model. To swap, the router publishes a new version (`orchestrator-v2.2`).
- The orchestrator pins versions explicitly via env config:
  ```bash
  ORCHESTRATOR_MODEL=orchestrator-v2.1
  VALIDATOR_MODEL=validator-v1.0
  WORKER_DEFAULT_MODEL=worker-default-v1.0
  WORKER_CODE_MODEL=worker-code-v1.0
  WORKER_VISION_MODEL=worker-vision-v1.0
  ```
- The router announces deprecation of a specialty version at least **30 days** in advance via the contract changelog (see Section 7).
- The router returns HTTP `410 Gone` with a structured error body if a deprecated specialty is requested after its sunset date.

### 2.3 Sampling parameters

Sampling parameters (temperature, top_p, frequency_penalty, presence_penalty, stop sequences, seed) are **baked into the specialty version** by the router. Clients may override `temperature`, `top_p`, and `max_tokens` per request, but the router treats other sampling params as fixed for a given specialty version. This guarantees that the same `model` + `messages` + override-set always produces the same distribution.

---

## 3. Request shape

A complete request, with all supported fields. Required vs optional as marked.

```json
{
  "model": "orchestrator-v2.1",            // required, specialty name
  "messages": [                            // required, OpenAI standard
    {"role": "system", "content": "..."},
    {"role": "user",   "content": "..."}
  ],

  "max_tokens":   4096,                    // optional, overrides specialty default
  "temperature":  0.0,                     // optional, overrides specialty default
  "top_p":        1.0,                     // optional, overrides specialty default
  "stream":       false,                   // optional, default false

  "response_format": {                     // optional, OpenAI Structured Outputs
    "type": "json_schema",
    "json_schema": {
      "name":   "TaskGraph",
      "schema": { /* full JSON Schema */ },
      "strict": true                       // grammar-constrained decoding
    }
  },

  "tools": [                               // optional, OpenAI Tool Calling
    {
      "type": "function",
      "function": {
        "name":        "search_corpus",
        "description": "Semantic search of grounded sources.",
        "parameters":  { /* JSON Schema */ }
      }
    }
  ],
  "tool_choice": "auto",                   // optional, OpenAI standard

  "metadata": {                            // optional, OpenAI standard (≤16 KV pairs)
    "run_id":   "01HV5RZP1...",            // UUIDv7
    "step_key": "t1",
    "trace_id": "0123abcdef..."            // 32-hex W3C TraceContext trace-id
  }
}
```

**Trace propagation (header):** in addition to `metadata.trace_id`, clients send the W3C `traceparent` header on every request:

```
traceparent: 00-0123abcdef0123456789abcdef012345-89abcdef01234567-01
```

Router behavior with `traceparent`: see [Section 6, commitment #3](#6-commitments-from-the-router-team).

### 3.1 Image inputs (multimodal workers only)

For `worker-vision-v1.0`, images use the standard OpenAI content-parts format:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Extract throughput data from this chart."},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
  ]
}
```

Data URLs and HTTPS URLs are both accepted. Max image size and total payload limits are specialty-specific and documented in the router's `/v1/models` metadata.

---

## 4. Response shape

Standard OpenAI response body, plus three router-specific headers and standard trace propagation.

### 4.1 Body (OpenAI standard)

```json
{
  "id":      "chatcmpl-01HV5...",
  "object":  "chat.completion",
  "created": 1735862400,
  "model":   "orchestrator-v2.1",          // echoes the requested specialty
  "choices": [
    {
      "index":   0,
      "message": {
        "role":      "assistant",
        "content":   "...",                 // null if a tool_call was made
        "tool_calls": [/* OpenAI tool_call objects */]
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens":     1240,
    "completion_tokens": 320,
    "total_tokens":      1560
  }
}
```

### 4.2 Required response headers

The router commits to setting these on **every** response, including error responses.

| Header | Format | Meaning | Example |
|---|---|---|---|
| `x-llm-model-served` | string | The concrete model that handled this request. Stable for the lifetime of a specialty version. | `Qwen/Qwen3-4B-Instruct` |
| `x-llm-route-decision` | `<specialty>@<model-id>[?reason=...]` | Router's resolution log. Reason qualifier is for debugging routing logic. | `orchestrator-v2.1@qwen3-4b` |
| `x-llm-cost-usd` | decimal string | Cost of this call in USD, computed from token counts × model price. | `0.0012` |

### 4.3 Trace propagation (response header)

The router echoes a `traceparent` header reflecting the span it created for this request. Clients append it to their trace tree:

```
traceparent: 00-0123abcdef0123456789abcdef012345-fedcba9876543210-01
```

The `trace-id` (middle field) is identical to the request's. The `parent-id` is the router's child span.

### 4.4 Streaming responses

Standard OpenAI Server-Sent Events format. Headers in Section 4.2 are set on the **initial SSE response**, not on each event chunk. Usage and cost headers may be omitted on streaming responses if the router can't compute them until the stream completes; in that case the **final SSE event** (`data: [DONE]`) is preceded by a synthetic event with `usage` and `cost_usd` in the body.

---

## 5. Errors

Standard OpenAI error envelope:

```json
{
  "error": {
    "message": "Specialty 'orchestrator-v1.0' was deprecated on 2026-03-15.",
    "type":    "specialty_deprecated",
    "param":   "model",
    "code":    "specialty_deprecated"
  }
}
```

### 5.1 Status code mapping

| Status | When the router returns it |
|---|---|
| `400 Bad Request` | Malformed request body, unknown field, invalid `response_format` schema. |
| `401 Unauthorized` | Missing / invalid bearer token. |
| `403 Forbidden` | Token lacks scope for the requested specialty. |
| `404 Not Found` | Specialty does not exist (never was published). |
| `410 Gone` | Specialty existed but has been deprecated past its sunset date. Response body includes the recommended replacement specialty. |
| `422 Unprocessable Entity` | The model could not satisfy a `strict: true` JSON schema after the router's internal retry budget. |
| `429 Too Many Requests` | Rate limit hit. Includes standard `Retry-After` header. |
| `500 Internal Server Error` | Router-side bug. |
| `502 Bad Gateway` | Upstream model server unreachable / crashed. |
| `503 Service Unavailable` | Router shutting down or in maintenance. |
| `504 Gateway Timeout` | Upstream model exceeded its deadline. |

### 5.2 Retry guidance for clients

The orchestration platform retries on `502`, `503`, `504`, and transport errors (connection reset, read timeout). It does **not** retry on `4xx` codes — those indicate the request itself needs to change.

---

## 6. Commitments from the router team

These seven commitments are what make the boundary actually safe. They are testable and should be part of the router team's acceptance criteria.

### 6.1 Versioned specialty names

Specialty names always include an explicit version (`orchestrator-v2.1`). Clients never see `orchestrator-latest`. Router-side, an unversioned name is a `404`.

### 6.2 Strict pinning, no fallback

For specialties listed in [Section 2.1](#21-initial-specialties), the router routes the request to one model. There is no auto-fallback to a different model on capacity issues; capacity issues surface as `503` and the client retries or queues.

### 6.3 W3C TraceContext propagation

If the request includes a `traceparent` header, the router:
- Uses the supplied `trace-id` for the span it creates.
- Echoes a `traceparent` in the response with the same `trace-id` and the router's child span as `parent-id`.
- Forwards `traceparent` to the upstream model server if that server emits OTel traces.

If no `traceparent` is supplied, the router generates one and sets it in the response.

### 6.4 Parameter immutability within a specialty version

Sampling parameters not overridable by the client (everything except `max_tokens`, `temperature`, `top_p`) are fixed for the lifetime of a specialty version. The router will not silently re-tune `frequency_penalty`, `repetition_penalty`, or stop sequences mid-version.

### 6.5 Server-side grammar-constrained decoding

When `response_format: {type: "json_schema", strict: true}` is supplied, the router enables grammar-constrained decoding (vLLM `guided_json` or equivalent) on the upstream model. The response is **guaranteed** to be JSON that validates against the supplied schema. If the model cannot satisfy this within the router's internal retry budget, the response is `422 Unprocessable Entity` — never a body that fails schema validation.

### 6.6 Always-set telemetry headers

The three headers in [Section 4.2](#42-required-response-headers) — `x-llm-model-served`, `x-llm-route-decision`, `x-llm-cost-usd` — are set on every response, including error responses (`4xx`/`5xx`). For errors, `x-llm-cost-usd` is `0`.

### 6.7 Deprecation notice

The router publishes a `deprecated_at` and `sunset_at` for every specialty version. Sunset is at least **30 days** after the announcement. The deprecation is visible at:
- The router's `/v1/models` endpoint (standard OpenAI listing) — each model includes `deprecated: bool` and `sunset_at: ISO8601`.
- The contract changelog in [Section 7](#7-changelog).

Requests for a deprecated-but-not-yet-sunset specialty return `200` with a `Warning` header:

```
Warning: 299 - "Specialty orchestrator-v2.0 is deprecated; sunset 2026-05-24. Migrate to orchestrator-v2.1."
```

After sunset, requests return `410 Gone`.

---

## 7. Changelog

| Date | Specialty | Change |
|---|---|---|
| 2026-04-24 | (all) | Initial contract published. |

Future entries should record: addition of a new specialty version, deprecation of an existing version, contract amendments, and breaking-change incident postmortems.

---

## 8. Quick reference: client code (illustrative)

For implementers on the orchestration side. The router contract is OpenAI-compatible so any SDK works.

```python
from openai import AsyncOpenAI
from pydantic import BaseModel

client = AsyncOpenAI(
    base_url=os.environ["LLM_TIER_ENDPOINT"],   # e.g. https://router.internal/v1
    api_key=os.environ["LLM_TIER_TOKEN"],
)

# Structured-output orchestrator call
response = await client.chat.completions.create(
    model="orchestrator-v2.1",
    messages=[{"role": "system", "content": SYS}, {"role": "user", "content": query}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name":   "TaskGraph",
            "schema": TaskGraph.model_json_schema(),
            "strict": True,
        },
    },
    metadata={"run_id": run_id, "step_key": "orchestrate", "trace_id": trace_id},
    extra_headers={"traceparent": traceparent},
    max_tokens=4096,
    temperature=0.0,
)

# Inspect telemetry headers
served = response.response.headers["x-llm-model-served"]
cost   = float(response.response.headers["x-llm-cost-usd"])

# Body is guaranteed to validate
task_graph = TaskGraph.model_validate_json(response.choices[0].message.content)
```

---

## 9. Out of scope

The following are **not** part of this contract. They live in the orchestration platform, not the router.

- Task decomposition prompts (`backend/agents/orchestrator.py`)
- Validator success-criteria rubrics (`backend/agents/validator.py`)
- MCP tool definitions and execution (`mcp_servers/`)
- Persistence of jobs, runs, steps, attempts, connectors (`backend/db/`)
- Scheduling and cron evaluation (APScheduler)
- Observability dashboards (Langfuse self-host)

If a behavior is in this list, the router team is not responsible for it.
