# Auto-Decomposition Layer (ADL) — Architecture and Implementation Plan (v2)

Working name: **ADL**. A standalone service that takes one natural-language objective, decomposes it into a set of agents automatically, runs them through the existing tier router as an external gateway, tracks the agents it creates, can run them on a recurring schedule, and draws tools from a managed on-box toolbox. Built on `deepagents`, deployed single-node on the R470, API-first with a CLI now and a web dashboard later.

This document is the implementation brief for Claude Code. It states target interfaces, the data model, the build order, and the decisions already made so implementation does not re-litigate them.

---

## 0. Standalone boundary (read first)

ADL is its own repository and its own process. It does **not** get bolted into the router project.

The only coupling to the router is an HTTP call to the gateway's OpenAI-compatible `/v1` endpoint. There are no imports from the router codebase, no shared database, and no shared deployment unit. The router is treated as an external dependency with two contract points:

1. The OpenAI-compatible chat completions endpoint on the gateway (the interactive server, default `:8900`).
2. A **tier selector** in the `model` field: `auto` for worker calls, or `tier1..tier5` to pin a structural role. The gateway owns model identity, so ADL holds no model names and a backend model swap needs no change in ADL.

If the gateway is unreachable, ADL fails the run cleanly and reports it. Nothing about ADL's internals leaks into the router and nothing about the router's internals leaks into ADL beyond those two contract points.

---

## 1. Goals and success criteria

**Thesis.** Decomposition and the tier router reinforce each other. Smaller, more uniform subtasks give the router cleaner inputs, so each agent lands on the cheapest sufficient tier. Simple branches route to T1/T2, only the high-stakes steps pull T4/T5. The demo makes that mechanism visible, manageable, and repeatable.

**Hard requirements.**
1. The only human input is the objective string, plus optional approval of the auto-generated plan. No user authors agents.
2. ADL is standalone and calls the router as a single external gateway (§0).
3. Most calls leave the tier to the router. Specific calls (plan creation, synthesis) can **force** a tier through the API call shape.
4. The run is fully observable and the agents it creates are **tracked** in a durable registry.
5. Tracked agents can **recur** on a schedule.
6. Agents draw tools from a managed **on-box toolbox**, granted per role.
7. A cost rollup compares decomposed-and-routed execution against a monolithic top-tier baseline.

**Success criteria (demo).** Submit an objective, watch agents spawn and route across tiers in real time, see a final answer and a cost table. Inspect and approve the plan before execution. Re-run a saved objective, or schedule it to recur. Show the toolbox and which roles hold which tools.

**Non-goals (this phase).** Multi-tenant isolation, horizontal scale. Single-node throughout, with these as later additions.

---

## 2. System architecture

```
                          ┌──────────────────────────────────────────────┐
   objective (text)       │                 ADL service                   │
   ──────────────────────▶│  FastAPI  ──▶  Orchestrator (deepagents)      │
                          │     │              │                          │
   CLI / web client       │     │              ├─ Planner   (forced tier) │
   ◀────── SSE events ────┤     │              ├─ Subagents (auto tier)   │
                          │     │              ├─ Synthesis (forced tier) │
                          │     │              └─ Virtual filesystem       │
                          │     │                                          │
                          │     ├─▶ ModelFactory ── tier override / auto ──┼──┐
                          │     ├─▶ Registry + Scheduler (tracking,        │  │
                          │     │      recurrence)                         │  │
                          │     ├─▶ Toolbox client (MCP)  ─────────────────┼──┼─▶ Toolbox MCP server (on box)
                          │     └─▶ Observability: callbacks + streams     │  │
                          │              │                                 │  │
                          │              ▼                                 │  │
                          │      SQLite (runs, agents, calls, templates,   │  │
                          │              schedules, events)                │  │
                          └──────────────────────────────────────────────┘  │
                                         │ OpenAI-compatible /v1             │
                                         ▼                                   │
                          ┌──────────────────────────────────────────────┐  │
                          │   vllm-sr / hybrid_ai gateway  (T1..T5)       │◀─┘
                          │   ├─ small model server  (T1/T2)  ◀ concurrent
                          │   └─ 30B / 80B server     (T4/T5)  ◀ concurrent
                          └──────────────────────────────────────────────┘
                                         (all on the R470)
```

**Request lifecycle.**
1. Client POSTs an objective (or the scheduler fires a saved template). ADL creates a run and a LangGraph thread keyed by `run_id`.
2. The planner pass, **forced to a high tier**, decomposes the objective into a todo DAG and binds each item to a role profile or the general-purpose worker.
3. The decomposition is recorded in the registry. If approval mode is on, the run interrupts and waits.
4. Independent agents dispatch concurrently. Worker calls go out as **auto** so the router classifies them. Dependency outputs pass downstream through the virtual filesystem.
5. A synthesis agent, **forced to a high tier**, composes the final answer.
6. Observability writes per-call and per-agent records and emits SSE throughout. The registry records the run and its agent lineage.

---

## 3. Foundation: deepagents and the router

Pin `deepagents`, `langchain`, `langchain-openai`, `langgraph`. Verify `subagents`, `interrupt_on`, and the filesystem permission schema against the installed reference at `reference.langchain.com/python/deepagents` before coding.

### 3.1 ModelFactory

ADL never holds a model name. It holds a factory that produces a chat model bound to a tier **selector**. The full implementation is `adl/model.py`; the contract it encodes is below.

The gateway is the interactive server (default `http://localhost:8900`), an OpenAI-compatible Chat Completions endpoint. ADL calls this, not vllm-sr (:8899) directly. The `model` field is a **tier selector, not a model name**:
- `model="auto"` lets the router classify and pick the tier.
- `model="tier1".."tier5"` pins that tier. A real model id is rejected with 400.

Model identity is owned by server config and never leaves the gateway, so ADL holds no model names and a backend model swap needs no client change. The rest of ADL speaks in tiers (`T1..T5`); the factory normalizes those to the wire selector (`tier1..tier5`) in one place.

```python
# adl/model.py (abridged)
class ModelFactory:
    def auto(self, temperature=0.2):           # model="auto"
        ...
    def for_tier(self, tier, temperature=0.2): # 'T5' -> model="tier5"
        ...
```

### 3.2 Reading the routing decision

The gateway returns its decision in response headers on any non-cached 2xx, already mapped to tier ids:
- `x-vsr-selected-model` (the tier that served it, e.g. `tier3`), `x-vsr-selected-category`, `x-vsr-selected-reasoning`, plus `x-vsr-selected-confidence`, `x-vsr-selected-decision`, and `x-vsr-matched-*` when present.

Build the chat model with `include_response_headers=True` so langchain-openai surfaces these in `response_metadata["headers"]`. On a **cache hit the headers are absent**, so fall back to the body `model` field, which is also a tier id. No model-id inverse map is needed: the gateway already returns tiers. Implemented in `adl/observability/callbacks.py`.

### 3.2a Auth, streaming, token budget

Auth follows `SR_AUTH_MODE`. `open` (local, default) needs nothing; `access` is handled by the Cloudflare Access edge; `proxy` requires `X-Auth-Email` and `X-Proxy-Secret` on every request or it 403s. ADL adds the proxy headers via `default_headers` when configured.

Streaming is not supported by the gateway (400). Disable model-level token streaming (`disable_streaming=True`). ADL's own event streaming (SSE for the CLI and web) is separate and unaffected, which also settles the earlier token-vs-event question: the surface is event-level.

Token budget: send `max_completion_tokens`. The gateway auto-retries as `max_tokens` upstream when an adapter needs it, so ADL needs no retry of its own.

### 3.3 Where forcing applies

- **Planner / decomposition:** forced high (default T5). Plan quality sets the ceiling for the whole run, so it does not get routed down.
- **Synthesis:** forced high (default T4, configurable to T5).
- **Workers:** **auto**. This is the demo's point. Smaller subtask prompts let the router pick the cheapest sufficient tier per agent.

Tier policy is config, not code:
```
ADL_PLANNER_TIER=T5
ADL_SYNTHESIS_TIER=T4
ADL_WORKER_TIER=auto
```

### 3.4 create_deep_agent assembly

```python
from deepagents import create_deep_agent
from langgraph.checkpoint.sqlite import SqliteSaver

mf = ModelFactory()

agent = create_deep_agent(
    model=mf.for_tier(os.environ["ADL_PLANNER_TIER"]),     # orchestrator/planner forced
    tools=toolbox_tools,                                    # from the toolbox MCP client, §5
    system_prompt=ORCHESTRATOR_PROMPT,
    subagents=ROLE_PROFILES,                                # workers default to mf.auto(), §4
    permissions=FS_PERMISSIONS,
    interrupt_on=INTERRUPTS,
    checkpointer=SqliteSaver.from_conn_string(os.environ["ADL_CHECKPOINT_DB"]),
)
```

**Filesystem backend:** local disk rooted at `/var/lib/adl/workspace/<run_id>`, the shared medium for inter-agent data.

**Prompt caching:** not automatic through the router. Enable vLLM automatic prefix caching on the model servers to cache the repeated system-prompt prefix across subagent calls.

---

## 4. Agent management model

### 4.1 Auto-synthesis, not hand-authoring

The user supplies only the objective. Agents are produced two ways, both system-driven:

- **Planning pass** (forced tier): the orchestrator decomposes the objective with `write_todos` into 2 to 5 items, each with a short role, an instruction, dependency ids, and an expected tier.
- **Profile binding:** each todo binds to a system-shipped role profile (custom subagent) or the general-purpose fallback. The user never authors a profile; the planner selects and parameterizes them.

**Initial profiles.** Each carries a tool grant from the toolbox (§5) and a tier policy (workers are `auto`).

| Profile | Purpose | Toolbox grant | Tier |
| --- | --- | --- | --- |
| `researcher` | gather and summarize facts | `kb_lookup`, `web_search`, fs read/write | auto |
| `analyst` | reason, compare, quantify | `calculator`, `metrics_query`, fs read/write | auto |
| `coder` | write and run small code | `eval` (QuickJS), fs read/write | auto |
| `writer` | compose deliverables | fs read/write | auto |
| `general-purpose` | fallback | default set | auto |

Confirm the subagent dict shape (`name`, `description`, `system_prompt`, `tools`, optional `model`) against the installed reference; bind worker `model` to `mf.auto()`.

### 4.2 Inter-agent data flow

Subagents are stateless with a single handoff. Pass dependency outputs by short direct context for small results, and by the shared filesystem for anything large or reused. Permissions confine every agent to its run workspace.

### 4.3 Concurrency and the DAG

Independent todos dispatch concurrently via async subagents. The orchestrator runs the DAG in dependency layers, then synthesis.

**Concurrency realism on one CPU box.** Logical parallelism is not wall-clock parallelism. To overlap in real time, map at least two tiers to distinct co-resident model servers: a small always-resident model for T1/T2 and the 30B/80B server for T4/T5. Validate overlap with the existing benchmark harness. The demo's primary value is cost-shaping and management visibility; treat latency as a measured claim, not an assumed one.

### 4.4 Governance and budgets

Per-run budget middleware: `max_subagents` (default 6), `max_tool_hops` per agent (default 5), `max_total_tokens`, optional per-tier ceilings. On breach, stop cleanly and let synthesis work from partial results.

### 4.5 Human-in-the-loop

Two interrupt gates: plan approval after decomposition, and a sensitive-tool gate via `interrupt_on` (for example any `eval`, or any write outside the workspace). Approval resumes with `Command(resume=...)` using `thread_id == run_id`.

### 4.6 Lifecycle and failure handling

Spawn, run, single handoff, ephemeral teardown. On subagent error retry once; on second failure mark failed and pass partial context to synthesis. Validate the DAG (cycles, missing ids) before execution.

---

## 5. The agent toolbox and integrations

### 5.1 Concept

Tools are not hardcoded into agents. They live in a small **toolbox service** on the box, and roles are **granted** subsets. This demonstrates how an enterprise would manage the set of tools available to agents: one managed catalog, per-role access, auditable usage.

### 5.2 Toolbox as a local MCP server

Stand up a small MCP server on the R470 (FastMCP or equivalent) that hosts the demo tools. ADL connects as an MCP client and passes the granted tools into `create_deep_agent` and into each profile. The toolbox runs as its own process so it can be inspected, restarted, and extended independently of ADL.

**Initial toolbox (small, real, offline-friendly):**

| Tool | Kind | Behavior |
| --- | --- | --- |
| `kb_lookup` | read | query a local SQLite/markdown knowledge base of canned facts |
| `web_search` | read | mock corpus now, real provider later |
| `calculator` | compute | safe AST arithmetic |
| `unit_convert` | compute | unit and currency conversions from a static table |
| `metrics_query` | data | read the benchmark/router metrics SQLite (ties to existing data) |
| `ticket_create` | action | write a mock ticket row, to show a side-effecting tool under HITL |

`metrics_query` is the bridge to existing work: an `analyst` agent can pull measured tier economics. `ticket_create` is the example of an action tool that should sit behind the sensitive-tool interrupt.

### 5.3 Tool governance

Grants are declared per profile (§4.1) and enforced by passing only the allowed MCP tools to each subagent. The registry records which tools each agent actually called, so a run shows both the grant and the usage. This is the management story for tools: catalog, grant, audit.

### 5.4 Router integration and tier capture

The model is the gateway. Tier capture is out-of-band via a LangChain callback reading the `x-vsr-selected-*` headers from `response_metadata` per call (see §3.2). The header value is already a tier id, so there is no model-id inverse map; the handler just normalizes `tier3` to `T3`. It also captures category, the reasoning toggle, confidence, decision, and matched signals, which enrich the management view ("router classified this as math, reasoning on, confidence 0.9"). On a cache hit the header is absent, so it falls back to the body model field, also a tier id. Full implementation in `adl/observability/callbacks.py`.

To record requested vs observed tier, tag each invocation with `tier_req:<tier>` (or `tier_req:auto`) via the runnable config; the handler reads it from `tags`. Pair the callback with `stream.subagents` (structural spawn/return) and join on run ids and tags.

### 5.5 Persistence

`SqliteSaver` for durable, resumable runs and interrupts. A separate SQLite schema (§7) for the management store, registry, and schedules. SQLite now, the same rows feed the web dashboard later.

---

## 6. Agent tracking and recurrence

This is a first-class capability, not a byproduct of logging. "Tracking" means a durable, queryable record of the agents ADL creates and their lineage. "Recurrence" means a tracked objective can run again on a schedule.

> Note on terms: recursive subagent spawning (agents creating agents) is already handled by the harness. This section is about **recurrence**, the scheduled re-execution of a saved objective. If the intent was different, this is the place to redirect.

### 6.1 Templates (the trackable, recurrable unit)

When a decomposition is produced, it can be saved as a named **template**: the objective, the config (tier policy, budgets, granted toolbox), and optionally a pinned plan so re-runs are deterministic. Templates are what you track over time and what you schedule.

### 6.2 Tracking and lineage

Lineage chain: `template → run → agent → call / tool_call`. The registry answers questions like: which agents has this template produced, how did tiers distribute across runs, which tools were used, how did cost trend over recurring runs. The `agents.parent_id` column captures subagent lineage within a run.

### 6.3 Recurrence scheduler

Use APScheduler with a SQLite jobstore so schedules survive restart. A schedule references a template and a trigger (cron or interval). On fire, the scheduler creates a new run from the template and records it under the schedule. Track per-fire status and link to the resulting run.

```
ADL_SCHEDULER=apscheduler
ADL_SCHEDULER_DB=/var/lib/adl/schedules.sqlite
```

Triggers: cron (`0 7 * * *`) and interval (`every 6h`). Enable/disable without deleting. A disabled schedule retains its history.

### 6.4 Management surface for tracking and recurrence

Registry and schedule views in the API (§8) and CLI (§9): list templates, show a template's run history and tier/cost trend, list schedules and next fire times, show a schedule's run history.

---

## 7. Observability and data model

```sql
CREATE TABLE templates (
  id TEXT PRIMARY KEY, name TEXT, objective TEXT,
  config_json TEXT,            -- tier policy, budgets, toolbox grant
  pinned_plan_json TEXT,       -- optional, for deterministic re-runs
  created_at TEXT
);

CREATE TABLE runs (
  id TEXT PRIMARY KEY, template_id TEXT, objective TEXT,
  status TEXT,                 -- planning|awaiting_approval|running|done|failed|aborted
  created_at TEXT, finished_at TEXT,
  total_cost REAL, baseline_cost REAL, savings_pct REAL
);

CREATE TABLE agents (
  id TEXT PRIMARY KEY, run_id TEXT, parent_id TEXT,
  profile TEXT, role TEXT, subtask TEXT,
  granted_tools TEXT,          -- the grant, for audit vs usage
  status TEXT, started_at TEXT, finished_at TEXT
);

CREATE TABLE calls (
  id TEXT PRIMARY KEY, agent_id TEXT, run_id TEXT,
  tier_requested TEXT,         -- 'auto' or forced tier
  tier_observed TEXT,          -- what the router actually used
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, ts TEXT
);

CREATE TABLE tool_calls (
  id TEXT PRIMARY KEY, agent_id TEXT, run_id TEXT,
  tool TEXT, args_digest TEXT, result_digest TEXT, latency_ms INTEGER, ts TEXT
);

CREATE TABLE schedules (
  id TEXT PRIMARY KEY, template_id TEXT,
  trigger_type TEXT,           -- cron|interval
  trigger_spec TEXT, enabled INTEGER, next_run_at TEXT, created_at TEXT
);

CREATE TABLE schedule_runs (
  id TEXT PRIMARY KEY, schedule_id TEXT, run_id TEXT,
  fired_at TEXT, status TEXT
);

CREATE TABLE events (
  id TEXT PRIMARY KEY, run_id TEXT,
  type TEXT,                   -- plan|spawn|route|tool|return|interrupt|synthesis|done
  payload_json TEXT, ts TEXT
);
```

**Cost rollup.** Per call, `routed_cost = tokens_out/1000 * cost(tier_observed)` and `baseline_cost = tokens_out/1000 * cost(T5)`. Roll up per run and trend across a template's recurring runs. Costs configurable per tier, seeded from measured economics, labeled illustrative until then.

---

## 8. API surface (FastAPI)

```
# runs
POST /v1/runs                     { objective | template_id, mode, approve?, budget?, tier_policy? } -> { run_id }
GET  /v1/runs/{id}/stream         # SSE
GET  /v1/runs/{id}                # state + summary
GET  /v1/runs/{id}/plan           # decomposition tree
POST /v1/runs/{id}/approve        { decision, edits? }
GET  /v1/runs/{id}/cost
GET  /v1/runs

# templates (tracking)
POST /v1/templates                { name, objective, config, pin_plan? } -> { template_id }
GET  /v1/templates                # list
GET  /v1/templates/{id}           # history, tier/cost trend, lineage

# schedules (recurrence)
POST /v1/schedules                { template_id, trigger_type, trigger_spec } -> { schedule_id }
GET  /v1/schedules                # list + next fire times
PATCH /v1/schedules/{id}          { enabled }
GET  /v1/schedules/{id}/runs

# toolbox
GET  /v1/toolbox                  # catalog of tools + which profiles are granted each
```

Run execution is a background task. Approval resumes the LangGraph thread. SSE is the single channel the CLI and the future web client consume. `tier_policy` in the run body lets a caller override planner/synthesis/worker tiers per run.

---

## 9. CLI

Thin client over the API using `httpx` and `rich`, consuming SSE. Port the renderer and cost table from the `auto_decompose.py` prototype.

```
adl run "objective" [--approve] [--planner-tier T5] [--worker-tier auto]
adl plan "objective"
adl runs | adl show <run_id> | adl cost <run_id>

adl template save "objective" --name nightly-brief [--pin-plan]
adl template ls | adl template show <id>          # history, tier/cost trend, lineage

adl schedule add <template_id> --cron "0 7 * * *"
adl schedule ls | adl schedule disable <id> | adl schedule runs <id>

adl toolbox ls                                    # catalog + per-profile grants
```

---

## 10. Deployment on the R470

**Processes (all co-resident, independent units):**
1. vllm-sr / hybrid_ai gateway (existing).
2. Model servers behind it. Run at least two so tiers overlap; enable vLLM prefix caching.
3. **Toolbox MCP server** (`adl-toolbox`), its own systemd unit or compose service.
4. **ADL API** (`uvicorn`), its own unit; runs the embedded APScheduler.
5. SQLite files on local disk: checkpoints, management store, schedules.
6. Per-run workspaces under `/var/lib/adl/workspace/<run_id>`.

**Config (env):**
```
ROUTER_BASE                         # gateway (interactive server), default http://localhost:8900
ROUTER_BASE_URL                     # default $ROUTER_BASE/v1
ROUTER_API_KEY                      # unused by the gateway
ADL_PLANNER_TIER, ADL_SYNTHESIS_TIER, ADL_WORKER_TIER   # e.g. T5, T4, auto
ADL_MAX_COMPLETION_TOKENS
SR_AUTH_MODE                        # open | access | proxy
SR_AUTH_EMAIL, SR_PROXY_SECRET      # required only when SR_AUTH_MODE=proxy
ADL_CHECKPOINT_DB, ADL_STORE_DB, ADL_SCHEDULER_DB
ADL_WORKSPACE_ROOT
ADL_TOOLBOX_URL                    # local MCP endpoint
ADL_MAX_SUBAGENTS, ADL_MAX_TOOL_HOPS, ADL_MAX_TOTAL_TOKENS
TIER_COST_JSON
```

**Filesystem permissions** confine agents to their run workspace and deny credential access:
```python
FS_PERMISSIONS = [
    {"operations": ["read", "write"], "paths": ["/workspace/**"], "mode": "allow"},
    {"operations": ["read", "write"], "paths": ["**/.env", "**/secrets/**"], "mode": "deny"},
]
```

ADL and the toolbox are light glue. Inference dominates and stays on the tuned model servers.

---

## 11. Build phases for Claude Code

Each phase has an acceptance check. Do not advance until it passes.

**Phase 0 — Scaffold and router smoke test.** Pin deps, build `ModelFactory`, stand up one deep agent with one tool. Accept when `auto()` and `for_tier("T5")` both return sane answers through the gateway and appear in router logs with the expected tiers.

**Phase 1 — Auto-decomposition with profiles.** Orchestrator prompt, `write_todos` planning forced to planner tier, role profiles, profile binding, workers on `auto`. Accept when an objective yields a 2 to 5 item plan bound to profiles with zero hand-authored agents.

**Phase 2 — Dependencies and concurrency.** DAG layers, filesystem hand-off, async dispatch. Accept when independent agents overlap on two co-resident servers and a dependent agent consumes an upstream artifact.

**Phase 3 — Toolbox.** Stand up the MCP toolbox server, wire ADL as client, enforce per-profile grants. Accept when a `researcher` can call `kb_lookup` and cannot call `eval`, and `GET /v1/toolbox` lists the catalog and grants.

**Phase 4 — Observability and cost.** `RouteCaptureHandler`, `stream.subagents` consumer, the schema, the rollup, the `extract_tier` vocabulary from §3.2. Accept when a run yields complete `agents`, `calls` (with requested vs observed tier), `tool_calls`, and `events`, plus a correct cost table.

**Phase 5 — API and CLI.** FastAPI endpoints, background execution, SSE; CLI over the API. Accept when the CLI drives a full run end to end and renders the live trace from SSE.

**Phase 6 — Tracking and recurrence.** Templates, registry views, APScheduler with SQLite jobstore, schedule endpoints and CLI. Accept when a template re-runs on a cron trigger, the schedule records its runs, and the template view shows a tier/cost trend across runs.

**Phase 7 — HITL and budgets.** Plan-approval interrupt, sensitive-tool gate on `ticket_create` and `eval`, budget middleware. Accept when a run pauses for approval, resumes on approve, aborts on reject, and a budget stops a run cleanly with partial synthesis.

**Phase 8 — Web (later).** Dashboard reading the same `events` and tables, Supabase auth, Cloudflare proxy. Out of scope for the first build; the schema and API are already shaped for it.

---

## 12. Repository layout

```
adl/
  pyproject.toml                # pinned deepagents, langchain, langgraph, fastapi,
                                # apscheduler, mcp/fastmcp, rich, httpx
  adl/
    config.py                   # env, tier policy, tier cost map, budgets
    model.py                    # ModelFactory (§3.1)
    prompts.py                  # orchestrator + profile prompts
    profiles.py                 # ROLE_PROFILES with toolbox grants + tier
    agent.py                    # create_deep_agent assembly
    registry/
      store.py                  # templates, runs, agents, lineage
      schemas.py
    scheduler/
      service.py                # APScheduler wiring, fire -> run
    observability/
      callbacks.py              # RouteCaptureHandler, extract_tier
      stream.py                 # stream.subagents consumer
      cost.py                   # rollup + trend
    api/
      main.py routes.py sse.py
    cli/
      __main__.py
  toolbox/                       # standalone MCP server process
    server.py                   # FastMCP app
    tools/
      kb_lookup.py web_search.py calculator.py
      unit_convert.py metrics_query.py ticket_create.py
    data/
      kb.sqlite                 # canned facts for kb_lookup
  migrations/
    001_init.sql                # schema in §7
  deploy/
    adl.service adl-toolbox.service   # systemd units
  tests/
    test_planning.py test_tier_forcing.py test_grants.py
    test_cost.py test_recurrence.py
```

---

## 13. Risks and decisions already made

- **Standalone boundary.** ADL never imports from or shares storage with the router. Two contract points only: the `/v1` endpoint and the tier-override convention.
- **Version churn.** Pin everything; verify `subagents`, `interrupt_on`, permissions, and MCP client APIs against the installed reference. Snippets here are intent.
- **Trust-the-LLM security.** Enforce at the tool and filesystem boundary: per-profile grants from the toolbox, workspace-confined permissions, action tools (`ticket_create`) and `eval` behind HITL. Prefer QuickJS `eval` over host shell.
- **Caching.** No automatic prompt caching through the router; use vLLM prefix caching.
- **Concurrency is logical until proven physical.** Map tiers to distinct co-resident servers and measure.
- **Demo repeatability.** Low temperature, fixed seeds where supported, pinned-plan templates, and a canned objective set for stable buyer demos.

---

## 14. Open questions to confirm

Resolved by the reconfigured gateway: `model` is a tier selector (`auto` or `tier1..tier5`), so ADL holds no model names and a backend swap needs no client change; the decision returns in `x-vsr-selected-*` headers (already tier ids) with a cache-hit fallback to the body model; auth is `SR_AUTH_MODE`; token budget is `max_completion_tokens` with the gateway handling the `max_tokens` retry; streaming is not supported, so ADL's surface is event-level.

Still open:

1. **Concurrency map.** Which tiers map to which co-resident model servers today, and can two run concurrently as configured? Affects whether parallel agents overlap in wall-clock time.
2. **Recurrence intent.** Confirm recurrence means scheduled re-execution of a saved objective (§6), not something else.
3. **Toolbox scope.** Any existing systems to expose as toolbox tools first (the benchmark/metrics DB is the obvious candidate via `metrics_query`)?
