# Agent Orchestrator — Design Spec

**Mission:** demonstrate complete multi-agent orchestration on a single Xeon
server — prompt decomposition, agent creation, agent management (scheduling,
tracking, budgets, human approval), agent execution, and tiered output
validation — with all LLM inference delegated to an external semantic tier
router that serves easy queries on-box and escalates hard ones off-box.

The build philosophy is **open-source tools + glue code**: the run engine,
scheduler, persistence, and protocols are maintained upstream projects; this
repo contributes the assembly, the observability bridge, and the console.

## Design decisions

| Decision | Rationale |
|---|---|
| `deepagents` (over LangGraph) as the only run engine | A maintained deep-agent harness does planning + delegation; zero hand-authored agent graphs to maintain |
| Declarative worker roles (`config/worker_roles.yaml`) | Agents are created from a prompt at run time, bound to role profiles — adding a capability is a YAML edit |
| External router owns model identity | The orchestrator only ever says `auto` / `tier1..tier5`; the sibling semantic-router project classifies difficulty and picks the model (see `docs/router-contract.md`) |
| Routing telemetry from `x-vsr-*` headers | Every step attempt records requested vs served tier, category, and cache hits — the router's decisions are observable per agent |
| Tiered validation (L0 → L1 → L2) | Free mechanical checks on every output; cheap-judge / frontier graders only where a role declares them; bounded retry-on-critique (`docs/validation_directive.md`) |
| MCP tools over HTTP, granted per role | Tool servers run as separate containers; `researcher` gets `web_search`, others don't — grants visible at `GET /toolbox` |
| Two SQLite stores, two jobs | LangGraph checkpointer = live/resume state (engine-internal, disposable); app DB = durable system of record (jobs, runs, steps, attempts, validations, connectors). Same tool, clean boundary, no DB server |
| HITL plan approval via one-shot `submit_plan` gate | Interrupt at the main agent only (deepagents #554/#573 make subagent interrupts unsafe); resume via `POST /run/{id}/approve` |
| Budgets as safety ceilings | `max_subagents` / `max_tool_hops` / `max_total_tokens`; on breach the run stops cleanly and synthesizes from partial results |
| WebSocket CloudEvents 1.0 stream | The UI shows the live execution flow — agents spawning, working, validating, completing — not just a final answer |
| APScheduler cron jobs | Scheduled/recurring runs are app-level concerns outside the engine, persisted in the app DB |
| Single `docker compose up` | The whole demonstration — backend, console, MCP servers, metrics — runs on the one server |

## What this project deliberately does NOT do

- **Model inference / routing** — delegated to the external OpenAI-compatible
  router gateway (`:8900`).
- **Vector search / re-rank** — delegated to the sibling semantic-search
  service, reached through the `doc_retrieval` MCP proxy.

## Where to look

- `README.md` — architecture, API, quick start.
- `docs/decomposition_layer_plan.md` — ADL design (target architecture the
  current engine implements).
- `docs/router-contract.md`, `docs/validation_directive.md`,
  `docs/standards.md` — the live contracts.
- `docs/archive/` — completed migration plans, kept for history.
