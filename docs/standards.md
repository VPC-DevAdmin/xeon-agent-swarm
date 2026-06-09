# Standards Adoption

**Status:** Approved 2026-04-24 · Owner: agent-orchestration team

This document records the **industry standards** this project commits to. The principle is simple: every contract surface — wire formats, identifiers, time, tracing, events, schedules — adopts an industry-standard format unless there's a written reason to deviate. This keeps the project portable, replaceable, and easy to integrate with other tools.

For the specific contract with the LLM router, see [`router-contract.md`](./router-contract.md).

---

## 1. Contract surface map

| Surface | Standard | Notes |
|---|---|---|
| LLM Chat API | **OpenAI Chat Completions** | See [`router-contract.md`](./router-contract.md). |
| LLM structured outputs | **OpenAI Structured Outputs** (`response_format: {type: "json_schema", strict: true}`) | Server-side grammar-constrained decoding. Replaces our prior Instructor + MD_JSON workaround. |
| LLM tool calling | **OpenAI Tool Calling** (`tools` + `tool_calls`) | Used for any tools that aren't behind MCP. |
| LLM streaming | **OpenAI SSE** | Standard chunked event stream. |
| Token / cost accounting | **OpenAI `usage` object** + `x-llm-cost-usd` header | Cost header is a router commitment, see [`router-contract.md` §4.2](./router-contract.md#42-required-response-headers). |
| Tool server protocol | **MCP (Model Context Protocol)** | All tool servers in `mcp_servers/` speak MCP over JSON-RPC 2.0. |
| Internal agent task shape | **A2A (Agent-to-Agent) protocol** | Our `Task` schema aligns to A2A's `Task` model. See [§2.1](#21-a2a-alignment-of-internal-task-shape). |
| Distributed tracing | **W3C TraceContext** (`traceparent` header) + **OpenTelemetry GenAI Semantic Conventions** | Span attributes follow OTel GenAI spec; Langfuse consumes them natively. |
| WebSocket event payloads | **CloudEvents 1.0** envelope | All events the backend pushes to the frontend are wrapped in a CloudEvents envelope. See [§2.2](#22-cloudevents-envelope-for-websocket-events). |
| Outbound webhooks (future) | **CloudEvents 1.0** + HMAC-SHA256 signature header | Stripe-style timestamp + signature. |
| REST API documentation | **OpenAPI 3.1** | Generated automatically by FastAPI. |
| Identifiers (jobs, runs, steps, attempts) | **UUIDv7** | Time-ordered; better DB index locality than UUIDv4. |
| Date/time everywhere | **RFC 3339 / ISO 8601 UTC** | Postgres `TIMESTAMPTZ`; Pydantic serializes to RFC 3339. |
| Schedule expressions | **5-field POSIX cron** + **IANA timezone** | `croniter` for parsing. |
| Secrets encryption (v1) | **Fernet (RFC 7515-style AEAD)** | Master key in env; envelope encryption on top later. |
| Auth (deferred) | **OAuth 2.0 / OIDC** when needed | v1 ships behind a network boundary; single-user. |

---

## 2. Specific design notes

### 2.1 A2A alignment of internal task shape

Google's A2A (Agent-to-Agent) protocol publishes a standard `Task` model with these key fields: `id`, `state`, `messages` (input), `artifacts` (output), `history`. We align our internal step model to those names:

| Our concept | A2A name | Notes |
|---|---|---|
| `TaskSpec` (sub-task in a swarm run) | `Task` | Renamed in code; carries the contract fields (`objective`, `scope`, `success_criteria`, `deliverable_format`). |
| `AgentResult` | `TaskResult` | Includes `artifacts` (A2A standard) and validator verdict. |
| `Artifact` (already exists) | `Artifact` | Already A2A-shaped: typed payload, content map. |
| `TaskGraph` | `Plan` | The orchestrator's decomposition output. Not a direct A2A primitive but consistent. |
| `SwarmEvent` | `Event` (CloudEvents-wrapped) | See [§2.2](#22-cloudevents-envelope-for-websocket-events). |

User-facing concepts in the DB use job/run/step/attempt vocabulary (top-level entities), not A2A names, to avoid confusion with the in-run swarm primitives. See `docs/persistence.md` (to be written) for the DB schema.

### 2.2 CloudEvents envelope for WebSocket events

Every event the backend broadcasts is wrapped in the CloudEvents 1.0 structured-mode JSON envelope. Concrete schema:

```json
{
  "specversion": "1.0",
  "type":        "io.xeon.swarm.step.completed",
  "source":      "/runs/01HV5RZ...",
  "id":          "01HV5RZ...",
  "time":        "2026-04-24T22:35:00Z",
  "subject":     "step:t1",
  "datacontenttype": "application/json",
  "data": {
    "step_key":   "t1",
    "latency_ms": 8200,
    "confidence": 0.82,
    "result_excerpt": "..."
  }
}
```

#### Event type registry

`type` values are reverse-DNS prefixed (`io.xeon.swarm.*`) so we can mix our events with third-party CloudEvents on the same bus later. Initial registry:

| `type` | Emitted when | Notable `data` fields |
|---|---|---|
| `io.xeon.swarm.run.started` | New run begins | `query`, `validator_enabled` |
| `io.xeon.swarm.run.completed` | Run reaches terminal state | `status`, `wall_clock_ms` |
| `io.xeon.swarm.run.metrics` | Final metrics packet | `RunMetrics` shape |
| `io.xeon.swarm.plan.ready` | Orchestrator emitted task graph | `task_graph` |
| `io.xeon.swarm.step.started` | Worker begins | `step_key`, `type` |
| `io.xeon.swarm.step.token` | Streaming token (writing tasks) | `step_key`, `token` |
| `io.xeon.swarm.step.completed` | Worker output accepted | `step_key`, `latency_ms`, `confidence`, `result_excerpt` |
| `io.xeon.swarm.step.failed` | Worker errored | `step_key`, `error` |
| `io.xeon.swarm.step.killed` | User canceled | `step_key` |
| `io.xeon.swarm.validator.started` | Validator running on a step | `step_key`, `attempt` |
| `io.xeon.swarm.validator.approved` | Validator passed the output | `step_key` |
| `io.xeon.swarm.validator.rejected` | Validator failed the output | `step_key`, `correction_hint` |
| `io.xeon.swarm.step.retrying` | Worker re-attempts after rejection | `step_key`, `attempt` |
| `io.xeon.swarm.step.rejected_final` | Validator failed final attempt; output committed with warning | `step_key` |
| `io.xeon.swarm.reduce.started` | Reducer begins assembling document | — |
| `io.xeon.swarm.tts.started` | TTS rendering pass begins | `section` |
| `io.xeon.swarm.tts.completed` | TTS rendering pass complete | `section`, `audio_url` |
| `io.xeon.swarm.error` | Unhandled error | `error` |

Adding a new event type requires registering it here.

### 2.3 OpenTelemetry GenAI conventions

Spans for LLM calls follow the [OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions/tree/main/docs/gen-ai). Required span attributes:

| Attribute | Value source |
|---|---|
| `gen_ai.system` | `"openai"` (the wire format we speak) |
| `gen_ai.request.model` | The specialty name (`orchestrator-v2.1`) |
| `gen_ai.response.model` | Value of `x-llm-model-served` response header |
| `gen_ai.usage.input_tokens` | From `usage.prompt_tokens` |
| `gen_ai.usage.output_tokens` | From `usage.completion_tokens` |
| `gen_ai.request.temperature` | If supplied in request |
| `gen_ai.request.max_tokens` | If supplied in request |
| `gen_ai.response.finish_reasons` | From `choices[].finish_reason` |

The OpenTelemetry SDK + `opentelemetry-instrumentation-openai` produces these automatically when the `AsyncOpenAI` client is patched.

### 2.4 UUIDv7 for all IDs

[UUIDv7](https://datatracker.ietf.org/doc/rfc9562/) is time-ordered (millisecond Unix timestamp prefix) which gives us:
- Better Postgres B-tree index locality on `runs.started_at` (insertion at the end of the index, not random)
- Natural ordering when displaying recent items
- Same uniqueness guarantees as UUIDv4

We use the `uuid7` package (or stdlib `uuid.uuid7()` when available on the Python version we run). All `id` columns use UUIDv7 going forward. Existing UUIDv4 records (currently only in-memory) become irrelevant after the persistence migration.

### 2.5 Cron expressions

Standard 5-field POSIX cron: `minute hour day-of-month month day-of-week`. We do **not** adopt Quartz's 6/7-field variant (no seconds resolution; if you need second-level scheduling, use APScheduler's `IntervalTrigger` directly instead of cron).

Schedules are stored as `cron_expression TEXT` + `cron_timezone TEXT` (IANA TZ database name, e.g. `America/Chicago`). The orchestrator UI renders human-readable cron descriptions via `cron-descriptor` so users see "Every weekday at 8:00 AM" instead of `0 8 * * 1-5`.

---

## 3. Removed dependencies (de-standardization)

Adopting industry standards lets us drop several pieces of custom infrastructure we previously needed only because we deviated:

| Dropped | Replaced by | Reason |
|---|---|---|
| `instructor` library | OpenAI native `response_format: json_schema` | Standard structured outputs make Instructor's mode-juggling moot. |
| Custom MD_JSON / Tools mode fallbacks in `InferenceClient` | Single code path with strict JSON schema | Eliminated by router's commitment to `strict: true` support. |
| Custom retry-on-malformed-JSON | (none — grammar-constrained decoding guarantees validity) | Router enforces shape server-side. |
| Custom `SwarmEvent` payload format | CloudEvents 1.0 envelope | Standard envelope; data field carries our existing payloads unchanged. |
| Inline corpus pipeline (`backend/corpus/`) | External semantic-search endpoint accessed via MCP | Knowledge retrieval is a separate concern; offloaded to a sibling project. |
| Inline vLLM containers (`vllm-text`, `vllm-vision`) | External LLM router | LLM serving is a separate concern; offloaded to a sibling project. |
| Inline TEI embedding service | External semantic-search endpoint | Embedding is a knowledge-retrieval concern. |

---

## 4. Changelog

| Date | Change |
|---|---|
| 2026-04-24 | Initial standards adoption document. |
