# Xeon Agent Swarm — Backend API Reference

This document describes every endpoint, WebSocket event, and data model exposed by the
backend. It is intended as the integration spec for any frontend that connects to this
service (Lovable, custom dashboards, third-party clients, etc.).

---

## Connection

| Setting | Value |
|---|---|
| Default backend URL | `http://localhost:8000` |
| Frontend env var | `VITE_API_URL` (falls back to `http://localhost:8000`) |
| WebSocket URL | replace `http` → `ws` in the base URL, e.g. `ws://localhost:8000` |
| CORS | `*` — all origins, methods, and headers are permitted |

---

## REST Endpoints

### `POST /run` — Start a swarm run

Decomposes the query into a task graph, fans out specialist workers in parallel, and
streams progress events over WebSocket.

**Request body**
```json
{ "query": "string  (1 – 10 000 characters)" }
```

**Response `200`**
```json
{ "run_id": "550e8400-e29b-41d4-a716-446655440000" }
```

Connect to `WS /ws/{run_id}` immediately after to receive live events.

---

### `GET /run/{run_id}` — Fetch final result

Polls the persisted result for a completed run. Safe to call while the run is still
in-flight (returns partial state).

**Response `200`** — [`RunResult`](#runresult) object (see Models section)

**Response when not found**
```json
{ "run_id": "...", "status": "not_found" }
```

---

### `POST /run/{run_id}/kill` — Kill a running task

Cancels a specific worker task mid-execution. The WebSocket will emit a `task_killed`
event confirming cancellation.

**Request body**
```json
{ "task_id": "string" }
```

**Response `200`**
```json
{ "status": "killed" | "not_found",  "task_id": "string" }
```

---

### `POST /run/{run_id}/retry` — Retry a failed or killed task

Re-dispatches a single task within an existing run without restarting the whole
pipeline. The rebuilt context includes artifact data from its dependencies.

**Request body**
```json
{ "task_id": "string" }
```

**Response `200`**
```json
{ "status": "retrying" | "not_found",  "task_id": "string" }
```

---

### `GET /audio/{filename}` — Serve TTS audio

Serves the MP3 file generated for a run's executive summary.
`filename` must end with `.mp3`; responds `404` otherwise.

**Response** — `audio/mpeg` binary stream

The URL is provided in [`DocumentResult.tts_audio_url`](#documentresult) as a relative
path (e.g. `/audio/550e8400.mp3`). Prefix with the base URL to play it:
```
GET http://localhost:8000/audio/550e8400.mp3
```

---

### `GET /health` — Liveness check

```json
{ "status": "ok", "service": "xeon-agent-swarm" }
```

---

### `GET /metrics` — Prometheus metrics

Returns Prometheus text format. Useful for dashboards.

---

## WebSocket — `WS /ws/{run_id}`

Open immediately after `POST /run` returns a `run_id`. Send periodic pings to keep the
connection alive. The server closes the socket when the run finishes.

Every message is a JSON object matching this envelope:

```json
{
  "event":     "string  (EventType)",
  "run_id":    "string",
  "payload":   { },
  "timestamp": "2026-04-21T00:00:00.000Z"
}
```

### Event sequence

```
run_started
graph_ready
task_started  ×N  (fires for each task, possibly interleaved)
task_token    ×M  (writing task only — one per generated token)
task_completed | task_failed | task_killed  ×N
synthesis_started
run_completed
```

---

### `run_started`
```json
{ "payload": { "query": "string" } }
```

---

### `graph_ready`

The orchestrator has decomposed the query. Render the task graph.

```json
{
  "payload": {
    "query": "string",
    "reasoning": "string  (why tasks were chosen)",
    "tasks": [
      {
        "id":           "string  (8-char UUID fragment)",
        "description":  "string",
        "type":         "research | analysis | code | vision | fact_check | writing | summarization | general",
        "dependencies": ["task_id", "..."],
        "priority":     1
      }
    ]
  }
}
```

---

### `task_started`
```json
{
  "payload": {
    "task_id":    "string",
    "description":"string",
    "type":       "string  (TaskType value)",
    "model":      "string  (model name)",
    "hardware":   "cpu | gpu"
  }
}
```

---

### `task_token` *(writing task only)*

Emitted for each token the writing worker streams. Concatenate them to build a live
preview of the report being generated.

```json
{
  "payload": {
    "task_id": "string",
    "token":   "string  (text fragment)"
  }
}
```

---

### `task_completed`
```json
{
  "payload": {
    "task_id":    "string",
    "result":     "string  (plain-text summary)",
    "confidence": 0.85,
    "model_used": "string",
    "hardware":   "cpu | gpu",
    "latency_ms": 1234.5,
    "tool_calls": ["web_search", "..."],
    "artifacts": [
      {
        "type":         "ArtifactType (see below)",
        "content":      { },
        "worker_id":    "string",
        "confidence":   0.8,
        "source_chunks":[]
      }
    ]
  }
}
```

---

### `task_failed`
```json
{ "payload": { "task_id": "string", "error": "string" } }
```

---

### `task_killed`
```json
{ "payload": { "task_id": "string" } }
```

---

### `synthesis_started`
```json
{ "payload": { "task_count": 5 } }
```

---

### `run_completed`

Fetch `GET /run/{run_id}` after receiving this event to get the full
[`RunResult`](#runresult) including the structured document.

```json
{
  "payload": {
    "final_answer": "string  (markdown)",
    "latency_ms":   12345.6,
    "task_count":   5
  }
}
```

---

### `error`
```json
{ "payload": { "error": "string" } }
```

---

## Data Models

### `RunResult`
Top-level object returned by `GET /run/{run_id}`.

```typescript
interface RunResult {
  run_id:       string
  swarm:        SwarmState
  document:     DocumentResult | null   // structured report (present after run_completed)
  single_model: SingleModelResult | null // only when ENABLE_AB_COMPARISON=1
}
```

---

### `SwarmState`
```typescript
interface SwarmState {
  run_id:       string
  query:        string
  task_graph:   TaskGraph | null
  results:      Record<string, AgentResult>
  final_answer: string | null
  status:       TaskStatus
  started_at:   string   // ISO 8601
  completed_at: string | null
}
```

---

### `TaskGraph`
```typescript
interface TaskGraph {
  query:     string
  tasks:     TaskSpec[]
  reasoning: string
}

interface TaskSpec {
  id:           string
  description:  string
  type:         TaskType
  dependencies: string[]
  priority:     number
}
```

---

### `AgentResult`
```typescript
interface AgentResult {
  task_id:    string
  status:     TaskStatus
  result:     string        // plain-text summary
  artifacts:  Artifact[]
  confidence: number        // 0.0 – 1.0
  model_used: string
  hardware:   string
  latency_ms: number
  tool_calls: string[]
}
```

---

### `DocumentResult`

Structured intelligence report assembled by the writing worker. Present in
`RunResult.document` after a run completes successfully.

```typescript
interface DocumentResult {
  title:             string
  executive_summary: string
  sections:          DocumentSection[]
  code_snippets:     CodeSnippet[]
  key_findings:      string[]
  sources:           string[]
  diagram_mermaid:   string | null    // Mermaid.js source
  tts_audio_url:     string | null    // e.g. "/audio/{run_id}.mp3"
  artifacts:         Artifact[]       // all typed artifacts from all workers
}

interface DocumentSection {
  title:   string
  content: string    // markdown prose
  sources: string[]  // URLs
}

interface CodeSnippet {
  language:     string
  description:  string
  code:         string
  syntax_valid: boolean
}
```

---

### `Artifact`

Every specialist worker emits typed artifacts alongside its plain-text result.

```typescript
interface Artifact {
  type:          ArtifactType
  content:       Record<string, unknown>   // shape depends on type — see below
  worker_id:     string
  confidence:    number
  source_chunks: string[]
}
```

#### Artifact content shapes

| `type` | `content` shape |
|---|---|
| `table` | `{ caption, headers: string[], rows: string[][] }` |
| `chart` | `{ caption, chart_type: "bar"\|"line", x_label, y_label, series: [{ name, data: [{x,y}] }] }` |
| `diagram` | `{ mermaid: string, caption }` |
| `code` | `{ language, code, description, syntax_valid: boolean }` |
| `claim_verdict` | `{ claim, verdict: "supported"\|"unsupported"\|"uncertain", evidence, source_url }` |
| `citation_set` | `{ citations: [{ title, url, snippet }] }` |
| `extracted_data` | `{ description, data_points: [{ label, value, unit }], source_image }` |
| `prose` | `{ text, section_title }` |

---

### Enums

```typescript
type TaskType =
  | "research" | "analysis" | "code" | "vision"
  | "fact_check" | "writing" | "summarization" | "general"

type TaskStatus = "pending" | "running" | "completed" | "failed" | "killed"

type ArtifactType =
  | "prose" | "table" | "diagram" | "chart" | "code"
  | "claim_verdict" | "citation_set" | "extracted_data"
```

---

## Typical Integration Pattern

```typescript
// 1 — Start the run
const { run_id } = await fetch(`${API_BASE}/run`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query }),
}).then(r => r.json())

// 2 — Open WebSocket for live events
const ws = new WebSocket(`${API_BASE.replace('http', 'ws')}/ws/${run_id}`)

ws.onmessage = ({ data }) => {
  const event = JSON.parse(data)          // SwarmEvent envelope

  switch (event.event) {
    case 'graph_ready':
      // event.payload.tasks → render task graph
      break
    case 'task_started':
      // event.payload.task_id, .type, .model → update worker card
      break
    case 'task_token':
      // event.payload.token → append to writing preview
      break
    case 'task_completed':
      // event.payload.artifacts → render typed artifact components
      break
    case 'task_killed':
    case 'task_failed':
      // event.payload.task_id → show error state + retry button
      break
    case 'run_completed':
      // fetch final result
      fetch(`${API_BASE}/run/${run_id}`)
        .then(r => r.json())
        .then(result => {
          // result.document → DocumentResult with sections, key_findings, etc.
          // result.document.tts_audio_url → play audio if present
        })
      break
  }
}

// 3 — Kill a task
await fetch(`${API_BASE}/run/${run_id}/kill`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ task_id }),
})

// 4 — Retry a failed task
await fetch(`${API_BASE}/run/${run_id}/retry`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ task_id }),
})
```

---

## Role Colours & Icons (UI conventions from existing frontend)

These are the visual identifiers the existing frontend assigns per task type.
Lovable can reuse or restyle them.

| Role | Icon | Colour family |
|---|---|---|
| `research` | 🔬 | Blue |
| `analysis` | 📊 | Purple |
| `code` | 💻 | Cyan |
| `vision` | 👁️ | Pink |
| `fact_check` | 🔍 | Amber |
| `writing` | ✍️ | Green |
| `summarization` | 📝 | Teal |
| `general` | ⚙️ | Gray |
