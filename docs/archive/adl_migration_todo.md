# [ARCHIVED — migration complete] ADL / Tier-Router Migration — Status & Remaining Work

> **Archived 2026-07-02.** The remaining-work items below were resolved the same
> day: the orphaned evaluator/evals and `inference/client.py` were **deleted**
> (the L0/L1/L2 validators + synthesis grader are the quality story), the stale
> configs (`endpoints.yaml`, `decomposition_templates.yaml`, `tts.py`, legacy
> env vars) were removed, and — correcting item 5 — `scripts/adl_stage2_test.py`
> was verified to import only live modules and was **kept**. The cost story
> (item 3 / P4 surfacing) was **dropped by decision**, not completed. The
> "Caveats / gotchas" section remains accurate and useful.

> **Reconciled 2026-07-02.** This file was originally a takeover TODO written at the
> `d393d73` state (old swarm still running, deepagents modules unwired). That migration
> is now **complete through Stage 6 cutover and verified LIVE end-to-end on the R470**
> against the `:8900` gateway. The original phase-by-phase worklist is superseded — it is
> preserved in git history (commit `edda96a`). Below is what actually landed and the few
> items that genuinely remain.

Solo repo: commit straight to `main`, no branches/PRs.

---

## What landed (original phases → reality)

| Original phase | Status | Evidence |
| --- | --- | --- |
| DO-FIRST: venv + freeze + verify API + P0/P1 probe | **done** | `~/.venv-adl` (and repo `.venv`) have deepagents 0.6.10 + langchain stack; `requirements.txt` frozen; `scripts/adl_smoke.py` runs P0+P1 green on the box (`auto→tier1`, `for_tier(T5)→tier5`, 2–5 profile-bound subtasks) |
| P3: event adapter + persistence | **done** | `observability/event_adapter.py` maps the deepagents stream → CloudEvents (WS) + `Step`/`StepAttempt`/`validation` rows + cost rollup; verified live (complete rows, cost+savings) |
| P2: excise old engine + wire `main.py` | **done** | `main.py` runs `run_deepagents`→`build_agent`→`run_with_adapter`; GO list deleted (`swarm_graph`, `orchestrator`, `planner`, `worker`, `reducer` all gone). MCP tools wired via `agents/toolbox.py` as LangChain `StructuredTool` over the in-repo HTTP `/mcp` servers (a deliberate deviation from `langchain-mcp-adapters` — those servers are simplified JSON-RPC, not full streamable-HTTP MCP; `build_toolbox()` is the single swap point) |
| P4: tier + cost story | **mostly done** | `cost.py`, tier/cost DB columns, Prometheus metrics done; cost/savings surfaced in the `GET /run/{id}` **metrics blob** (`total_cost`/`baseline_cost`/`savings_pct`). Not done: dedicated `GET /runs/{id}/cost`, typed cost fields on the DTOs, and UI surfacing (see Remaining) |
| P5: scheduler rebind | **done** | `scheduling/scheduler.py` fires via `main.launch_run` (the shared deepagents entry, fresh `thread_id==run_id`); `croniter` next-fire unchanged |
| P6: validation + HITL | **done (exceeds plan)** | Validation was **not** deferred — full L0 mechanical / L1 judge / L2 synthesis grader (`observability/validation_judge.py`, `validate_l0`) with bounded retry, verified live. HITL plan-approval fully working via the one-shot `submit_plan` gate (main-agent only), approve+reject verified live. See the four HITL commits below. |
| P7: tests + scripts | **done** | 39 tests green against the deepagents core; `scripts/adl_smoke.py` added. Old `scripts/{test_run,dashboard,inspect_run,smoke_test}.py` drive the HTTP API and still work |

### Session hardening beyond the original TODO (2026-07-01/02, on `main`)
- `2ce2cd1` HITL approval race (404-then-hang) + structured `Command(resume={"decisions":[…]})`
- `de13f8e` one-shot `submit_plan` gate (replaces the unusable `write_todos` gate)
- `1fa37fb` `ck_runs_status` constraint allows `awaiting_approval` + `aborted`
- `e2b0d67` gateway connection resilience (`ROUTER_MAX_RETRIES` 3→6)
- `869fac7` budget defaults raised (30/16) + partial synthesis on breach + grader fairness + orchestrator-preamble fix

---

## What actually remains

1. **Evaluator / evals are orphaned AND still on the old client.** `agents/evaluator.py`
   + `evals/runner.py` still `import ... InferenceClient, llm_endpoint, llm_model_for`
   from `inference/client.py`, and nothing in the live path calls them (not invoked from
   `main.py` or `event_adapter.py`). Decide: **rewire** to `ModelFactory` (`mf.auto()` or a
   pinned tier) and re-attach as the async post-run eval, **or delete** the orphaned eval.
   Either way this is the prerequisite for #2.
2. **Delete `backend/inference/client.py`** — the last GO-list survivor. `model.py` only
   references it in comments (constants were salvaged); its sole real importer is the
   orphaned evaluator (#1). Delete once #1 is resolved.
3. **P4 surfacing (additive, safe anytime):**
   - `GET /runs/{id}/cost` endpoint (rollup from `StepAttempt` rows) — not added.
   - Typed tier/cost fields on the Run/Step DTOs (`schemas/`) — cost currently rides in the
     `metrics` JSON blob only.
   - Frontend `WorkerCard.tsx` (routed tier per agent) + `MetricsHUD.tsx` (savings figure)
     don't surface tier/cost yet.
4. **Reference cleanup (inert):**
   - `config/endpoints.yaml` still names legacy per-model endpoints (`$WORKER_CPU_ENDPOINT`
     …) — but it is **not imported anywhere in `backend/`**, so it's dead config; repoint at
     `:8900` or remove.
   - `env.example` retains legacy `*_MODEL` vars; drop them with `client.py`.
   - `agents/tts.py`, `config/decomposition_templates.yaml` — keep-or-shelve per demo scope.
5. **Stale script:** `scripts/adl_stage2_test.py` references removed swarm modules and would
   break if run — repoint or delete.

---

## Caveats / gotchas (still current)
- **PEP 668 on the box:** use `~/.venv-adl` (or the repo `.venv`; both now carry the
  deepagents stack + pytest) — never `--break-system-packages`.
- **`main.py` has no dotenv autoload.** Export env (or `set -a; source .env.adl`). `:8000`
  is vLLM, so bind the app elsewhere (we use `:8010`). Run headless server-side.
- **deepagents 0.6.10 bugs:** subagent edit/reject interrupts broken (#554) → HITL at the
  MAIN agent only (the `submit_plan` gate is main-only); subagents have no own checkpoint
  (#573) → the event adapter captures subagent activity from the **stream live**.
- **Planner tier governs synthesis** (same main agent); `ADL_SYNTHESIS_TIER` only bites if
  synthesis is split into a dedicated subagent.
- **SQLite schema = delete the `.db` file.** `create_all` won't ALTER; new columns/CHECK
  constraints (e.g. the `ck_runs_status` fix adding `awaiting_approval`/`aborted`) need a
  fresh `orchestrator.db`. Checkpointer DB (`CHECKPOINT_DB`/`ADL_CHECKPOINT_DB`) is separate.
- **Gateway contract:** `model` = `auto`/`tier1..tier5` only (real id → 400); read
  `x-vsr-selected-*` headers (absent on cache hit → body model); `max_completion_tokens`;
  `SR_AUTH_MODE` proxy needs `X-Auth-Email` + `X-Proxy-Secret`; streaming unsupported;
  connection can drop mid-request (retries absorb it — `ROUTER_MAX_RETRIES`).
- **Budgets are safety ceilings, not operating limits** (`ADL_MAX_TOOL_HOPS=30` per agent,
  `ADL_MAX_SUBAGENTS=16` per run, `ADL_MAX_TOTAL_TOKENS=0`=unlimited). On breach the graph
  is abandoned and finalize composes a **partial synthesis** from collected results.
- **Two stores, two jobs:** checkpointer replays live state; app DB serves history + UI.

## Env (see `.env.adl` / `env.example`)
`ROUTER_BASE` (=:8900), `ROUTER_BASE_URL`, `ROUTER_MAX_RETRIES`, `ADL_PLANNER_TIER` (T5),
`ADL_WORKER_MAX_COMPLETION_TOKENS`, `SR_AUTH_MODE` (+ `SR_AUTH_EMAIL`/`SR_PROXY_SECRET`),
`ADL_PLAN_APPROVAL` (+ `ADL_PLAN_TOOL`=submit_plan, `ADL_SENSITIVE_TOOLS`),
`ADL_MAX_{TOOL_HOPS,SUBAGENTS,TOTAL_TOKENS}`, `ADL_*_VALIDATOR_TIER`,
`CHECKPOINT_DB`/`ADL_CHECKPOINT_DB`, `DATABASE_URL`, `TIER_COST_T1..T5`.
