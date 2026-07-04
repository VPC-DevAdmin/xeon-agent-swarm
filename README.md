# Agent Orchestrator

A focused, standards-aligned **agent orchestration platform**. A single
**deepagents** deep agent decomposes a prompt into specialist worker subagents,
delegates each through an external LLM **tier router**, validates every output
with a tiered validator (mechanical → cheap-judge → frontier), synthesizes a
result, and scores quality — all durably persisted, observable, and schedulable.

> **Engine:** the Auto-Decomposition Layer (ADL), built on `deepagents` over
> LangGraph, is the only run engine. The earlier hand-rolled LangGraph "swarm"
> (orchestrator/worker/reducer/swarm_graph) was removed at cutover; see
> [`docs/decomposition_layer_plan.md`](docs/decomposition_layer_plan.md)
> (design) and [`docs/archive/`](docs/archive) (completed migration plans).

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
| **Prompt decomposition** | The deep agent plans (pinned to `ADL_PLANNER_TIER`) and delegates to declarative worker subagents (`config/worker_roles.yaml`); zero hand-authored agents |
| **Tier routing** | Workers run on `auto` so the router classifies each task's difficulty and picks the tier; requested vs served tier, category, and cache hits are recorded per attempt from the `x-vsr-*` headers |
| **Tiered validation** | Every step: L0 mechanical (free) → L1 cheap-judge (tier1/2) → L2 frontier; bounded retry-on-critique; a frontier grader on the final synthesis ([`docs/validation_directive.md`](docs/validation_directive.md)) |
| **Tool catalog** | A curated set of 23 tools/connectors (messaging, social, data stores, web, dev) users configure in the Tools gallery; the planner sees the enabled ones (`config/tool_catalog.yaml`) and routes tool-using subtasks to the `tool_user` worker. All 23 execute for real once configured — 3 builtin MCP tools + 20 API-backed (`tool_impls.py`), credentials from the encrypted connector store. Catalog at `GET /tools`, per-role grants at `GET /toolbox` |
| **Governance** | Per-run budgets (`max_subagents`/`max_tool_hops`/`max_total_tokens`, clean partial-synthesis stop) and HITL plan approval (`POST /run/{id}/approve`) |
| **Live monitoring** | Stream-driven `Step`/`StepAttempt`/`Validation` rows + WebSocket CloudEvents 1.0 |
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

Pipeline per run (ADL / deepagents):

```
prompt
  │
  ▼ deep agent plans (pinned tier) ──▶ [optional HITL plan approval]
  │
  ▼ delegates worker subagents (auto tier; per-role tool grants)
  │     each result: L0 mechanical ──▶ L1/L2 judge ──▶ [bounded re-dispatch]
  │     (event adapter streams Step/Attempt/Validation rows + WS CloudEvents)
  │
  ▼ main agent synthesizes ──▶ L2 frontier grader on the final answer
  │
  ▼ finalize: validation + routing rollup, persist ──▶ broadcast
```

The event adapter ([`backend/observability/event_adapter.py`](backend/observability/event_adapter.py))
bridges the deepagents typed stream onto the same WS + SQLite surfaces; the app DB
is the system of record, not the LangGraph checkpointer.

## Data model

```
Job ──< Run ──< Step ──< StepAttempt
 └──< JobConnector >── Connector ──< ConnectorSecret (Fernet ciphertext)
                                  AuditLog (every secret decryption)
```

## Quick start

Fastest path — no Docker, no config, no cloud calls (uses the bundled mock router):

```bash
make setup      # Python venv + frontend deps (once)
make demo       # mock router + backend + frontend; Ctrl-C stops all three
# open http://localhost:3000  ·  `make demo-live` runs against the real router
```

Full stack with Docker:

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

- Frontend: <http://localhost:3000> (New Task · Activity · Connectors)
- API docs: <http://localhost:8000/docs> (OpenAPI 3.1)
- Metrics: <http://localhost:8000/metrics> (Prometheus)

### Optional: Langfuse tracing

```bash
docker compose -f docker-compose.yml -f docker-compose.langfuse.yml up -d
# create a project in http://localhost:3001, paste keys into .env, restart backend
```

## REST API (selected)

```
POST   /run                      ad-hoc run ({query, plan_approval?} — pause for plan review)
POST   /run/{id}/approve         HITL: approve/reject a paused plan
POST   /run/{id}/kill            cancel an in-flight run
GET    /toolbox                  tool catalog + per-role grants + validator policy
POST   /jobs                     create job (query + optional cron schedule)
GET    /jobs                     list
GET    /jobs/scheduled           active cron jobs by next fire time
PATCH  /jobs/{id}                update (schedule, query, config)
POST   /jobs/{id}/pause|resume|archive|run-now
GET    /runs                     run history
GET    /runs/{id}                full detail (steps + attempts + eval scores)
POST   /connectors               create (secrets encrypted on save)
PUT    /connectors/{id}/secrets/{field}   set/replace a secret
GET    /connectors               list (secret field NAMES only — never values)
```

## Testing & iterating

```bash
# End-to-end smoke test: connector + secret hygiene, scheduled job lifecycle,
# run-to-completion, step/attempt detail, history, cleanup.
python3 scripts/smoke_test.py

# Watch one run live in the terminal (rich dashboard).
python3 scripts/dashboard.py --validator "your query here"

# Inspect a finished run's per-step outputs.
python3 scripts/inspect_run.py --latest
```

### Mock router (offline end-to-end testing)

```bash
# Terminal 1: OpenAI-compatible stand-in for the tier router — canned planner/worker/judge
# responses drive the real deepagents engine with ZERO cloud API calls (safe scale testing).
python scripts/mock_router.py                                    # port 8901 (MOCK_ROUTER_PORT)

# Terminal 2: point the engine at it, then run anything above as usual.
export ROUTER_BASE=http://localhost:8901 ROUTER_BASE_URL=http://localhost:8901/v1
```

## Key environment variables

| Var | Purpose |
|---|---|
| `ROUTER_BASE` / `ROUTER_BASE_URL` | external tier router (OpenAI-compatible gateway, default `:8900`) |
| `ADL_PLANNER_TIER` / `ADL_WORKER_TIER` | planner pinned high; workers on `auto` so the router classifies |
| `ADL_VALIDATION_DEFAULT_LEVEL` / `ADL_*_VALIDATOR_TIER` | tiered validator levels + tiers |
| `ADL_MAX_SUBAGENTS` / `ADL_MAX_TOOL_HOPS` / `ADL_MAX_TOTAL_TOKENS` | per-run budgets |
| `ADL_PLAN_APPROVAL` / `ADL_SENSITIVE_TOOLS` | HITL gates |
| `CHECKPOINT_DB` | LangGraph AsyncSqliteSaver file (live/resume state) |
| `DATABASE_URL` | SQLite file (aiosqlite); default `sqlite+aiosqlite:///./data/orchestrator.db` |
| `MASTER_ENCRYPTION_KEY` | Fernet key for connector secrets |
| `SCHEDULER_ENABLED` | `0` disables background job firing |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | enable tracing (else no-op) |

See [`env.example`](env.example) for the full list.

## Project layout

```
backend/
  agents/        core (deepagents assembly), profiles, toolbox
  inference/     ModelFactory — the single seam to the tier router (model.py)
  db/            SQLAlchemy models, async SQLite engine, create_all schema
  repositories/  jobs / runs / connectors data access + persistence facade
  routers/       /jobs /runs /connectors /toolbox REST
  scheduling/    APScheduler job scanner
  security/      Fernet secret encryption
  observability/ event_adapter, validation (l0/judge), cost, callbacks, metrics
  protocols/     MCP clients, A2A agent cards
frontend/        React + Vite console (Live Run, Jobs, Runs, Connectors)
mcp_servers/     web_search, code_exec, doc_retrieval (search proxy)
docs/            router-contract.md, standards.md
scripts/         smoke_test, dashboard, inspect_run
```
