# Agent Orchestrator

A focused, standards-aligned **agent orchestration platform**. It decomposes a
prompt into a validated graph of specialist sub-tasks, runs them in parallel
through an external LLM router, validates each output against its contract,
synthesizes a result, and scores quality — all durably persisted, observable,
and schedulable.

This project owns **orchestration**. It deliberately delegates two concerns to
sibling services it calls over the network:

- **LLM inference + model routing** → an external OpenAI-compatible **router**
  (semantic routing, on/off-system tiers, specialty models). See
  [`docs/router-contract.md`](docs/router-contract.md).
- **Knowledge retrieval** (vector search + re-rank) → an external **semantic
  search** service, reached through the `doc_retrieval` MCP proxy.

## What it does

| Capability | How |
|---|---|
| **Prompt decomposition** | Orchestrator agent emits a contract-based `TaskGraph` (A2A-aligned), structurally validated before any work starts |
| **Agent creation & monitoring** | Workers fan out via LangGraph `Send`; live state over WebSocket (CloudEvents 1.0 envelopes) |
| **Output evaluation** | Per-step validator (mechanical + LLM-judge) with retry loop; async quality evals per deliverable-format after each run |
| **Tool calls** | MCP servers (web search, code exec, doc retrieval proxy) |
| **Scheduled runs** | Cron-scheduled Jobs (APScheduler), overlap policies, durable history |
| **Orchestration** | Durable Jobs → Runs → Steps → Attempts in a SQLite file; full REST + UI |

## Standards

OpenAI Chat Completions + Structured Outputs · MCP · A2A vocabulary ·
CloudEvents 1.0 · W3C TraceContext · OpenAPI 3.1 · UUIDv7 · RFC 3339 · POSIX
cron. See [`docs/standards.md`](docs/standards.md).

## Architecture

```
                       ┌─ external: semantic router / LLM tiers ─┐
  client / cron ──▶ THIS PROJECT ──┤  OpenAI-compatible /v1/chat/completions  │
                       └──────────────────────────────────────────┘
       │                    │                    │
   REST + WS         MCP tool calls       trace export (optional)
       │                    │                    │
       ▼                    ▼                    ▼
  SQLite file         web/code/doc-proxy     Langfuse (optional overlay)
  (jobs, runs,             │
   steps, attempts,        └─▶ external: vector search + re-rank
   connectors+secrets)
```

Pipeline per run:

```
prompt
  │
  ▼ orchestrate ──▶ validate graph (structural rules) ──▶ [retry w/ critique]
  │
  ▼ fan-out workers (respect deps; cascade-fail on failed deps)
  │     each: execute ──▶ validate (contract) ──▶ retry-with-hint ──▶ commit
  │
  ▼ reduce ──▶ DocumentResult
  │
  ▼ finalize (persist) ──▶ async quality eval ──▶ broadcast
```

## Data model

```
Job ──< Run ──< Step ──< StepAttempt
 └──< JobConnector >── Connector ──< ConnectorSecret (Fernet ciphertext)
                                  AuditLog (every secret decryption)
```

## Quick start

```bash
cp env.example .env

# Generate the secret-encryption key and paste into .env as MASTER_ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Point at your router + search service in .env:
#   LLM_TIER_ENDPOINT=https://router.internal/v1
#   LLM_TIER_TOKEN=...
#   SEMANTIC_SEARCH_ENDPOINT=https://search.internal

docker compose up -d --build        # MCP servers + backend + frontend + prometheus
# the SQLite schema is created automatically on backend startup (no DB server)
```

- Frontend: <http://localhost:3000> (Live Run · Jobs · Runs · Connectors)
- API docs: <http://localhost:8000/docs> (OpenAPI 3.1)
- Metrics: <http://localhost:8000/metrics> (Prometheus)

### Optional: Langfuse tracing

```bash
docker compose -f docker-compose.yml -f docker-compose.langfuse.yml up -d
# create a project in http://localhost:3001, paste keys into .env, restart backend
```

## REST API (selected)

```
POST   /run                      ad-hoc run
POST   /jobs                     create job (query + optional cron schedule)
GET    /jobs                     list
GET    /jobs/scheduled           active cron jobs by next fire time
PATCH  /jobs/{id}                update (schedule, query, config)
POST   /jobs/{id}/pause|resume|archive|run-now
GET    /runs                     run history
GET    /runs/{id}                full detail (steps + attempts + eval scores)
POST   /runs/{id}/kill           cancel in-flight steps
POST   /connectors               create (secrets encrypted on save)
PUT    /connectors/{id}/secrets/{field}   set/replace a secret
GET    /connectors               list (secret field NAMES only — never values)
```

## Testing & iterating

```bash
# End-to-end smoke test: connector + secret hygiene, scheduled job lifecycle,
# run-to-completion, step/attempt detail, quality eval, history, cleanup.
python3 scripts/smoke_test.py

# Watch one run live in the terminal (rich dashboard).
python3 scripts/dashboard.py --validator "your query here"

# Inspect a finished run's per-step outputs.
python3 scripts/inspect_run.py --latest
```

## Key environment variables

| Var | Purpose |
|---|---|
| `LLM_TIER_ENDPOINT` / `LLM_TIER_TOKEN` | external router (OpenAI-compatible) |
| `ORCHESTRATOR_MODEL` / `VALIDATOR_MODEL` / `WORKER_DEFAULT_MODEL` | router specialty names |
| `SEMANTIC_SEARCH_ENDPOINT` | external vector-search service (via MCP proxy) |
| `DATABASE_URL` | SQLite file (aiosqlite); default `sqlite+aiosqlite:///./data/orchestrator.db` |
| `MASTER_ENCRYPTION_KEY` | Fernet key for connector secrets |
| `SCHEDULER_ENABLED` | `0` disables background job firing |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | enable tracing (else no-op) |

See [`env.example`](env.example) for the full list.

## Project layout

```
backend/
  agents/        orchestrator, worker, validator, reducer, tts
  graph/         LangGraph swarm (fan-out/fan-in, graph validation)
  inference/     router client (native structured outputs, retries, traceparent)
  db/            SQLAlchemy models, async SQLite engine, create_all schema
  repositories/  jobs / runs / connectors data access + persistence facade
  routers/       /jobs /runs /connectors REST
  scheduling/    APScheduler job scanner
  evals/         per-deliverable-format quality rubrics
  security/      Fernet secret encryption
  observability/ Prometheus metrics, W3C trace helpers, optional Langfuse
  protocols/     MCP clients, A2A agent cards
frontend/        React + Vite console (Live Run, Jobs, Runs, Connectors)
mcp_servers/     web_search, code_exec, doc_retrieval (search proxy)
docs/            router-contract.md, standards.md
scripts/         smoke_test, dashboard, inspect_run
```
