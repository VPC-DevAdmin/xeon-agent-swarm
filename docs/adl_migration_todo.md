# ADL / Tier-Router Migration — Takeover TODO

Handoff for a Claude Code instance running **on the R470 server** (where
`deepagents` can actually be installed and run, and the `:8900` gateway is
reachable). This is the live worklist; the *what and why* lives in
[`decomposition_layer_plan.md`](decomposition_layer_plan.md) (ADL target arch)
and [`deepagents_integration_reference.md`](deepagents_integration_reference.md)
(deepagents 0.6.10 verified API shapes). Read both before P2+.

Solo repo: commit straight to `main`, no branches/PRs.

---

## Where this stands

Done so far (on `main`):
- **Tier/cost scaffolding** (commit `78e548b`): `observability/cost.py` (rollup +
  savings, illustrative price table), tier/cost columns on `StepAttempt`
  (`tier_requested/tier_observed/category/reasoning/confidence/cache_hit`) and
  `Run` (`total_cost/baseline_cost/savings_pct`), Prometheus tier/cost metrics,
  new env vars in `env.example`, `tests/test_cost.py`.
- **P0 + P1 modules** (commit `d393d73`): `inference/model.py` (ModelFactory),
  `observability/callbacks.py` (RouteCaptureHandler), `agents/profiles.py`
  (worker_roles.yaml → SubAgent specs), `agents/core.py` (create_deep_agent
  assembly), `requirements.in` + deepagents stack in `requirements.txt`.

**The old engine still runs.** `main.py` invokes the existing LangGraph swarm
(`graph/swarm_graph.py` + `agents/{orchestrator,planner,worker,reducer}.py` +
`inference/client.py`). The new deepagents modules are present but **unwired**, so
`main` boots fine even before `deepagents` is installed (nothing imports `core.py`
at startup).

**Hard ordering rule:** do NOT delete the old engine (the GO list) or rewire
`main.py` until P1 passes on the box. Deleting the working swarm before the
deepagents path runs end-to-end breaks `main`.

---

## DO THIS FIRST — install + verify + capture the stream shape

The authoring environment could not install deepagents (PEP 668 + no run env), so
two things are unverified and block the event adapter: the exact
`create_deep_agent` signature and the `agent.astream(...)` event shape.

```bash
cd ~/work/repos/xeon-agent-swarm/backend
sudo apt install -y python3-venv python3-full
python3 -m venv ~/.venv-adl
~/.venv-adl/bin/pip install --upgrade pip
~/.venv-adl/bin/pip install -r requirements.in
~/.venv-adl/bin/pip freeze > requirements.txt    # lock the resolved set; commit it
~/.venv-adl/bin/python -c "import deepagents, inspect; print(inspect.signature(deepagents.create_deep_agent))"
~/.venv-adl/bin/python -c "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; print('saver OK')"
```

If `deepagents==0.6.10` won't resolve, loosen the pin to `deepagents` and adapt
`core.py`/`callbacks.py` to whatever version lands (verify the SubAgent dict keys
and `interrupt_on` schema against `inspect`).

**P0 + P1 acceptance gate (one probe):**
```bash
export ROUTER_BASE=http://localhost:8900
cd ~/work/repos/xeon-agent-swarm
~/.venv-adl/bin/python - <<'PY'
import asyncio, sys; sys.path.insert(0, ".")
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.agents.core import build_agent
async def main():
    async with AsyncSqliteSaver.from_conn_string("/tmp/adl_probe.db") as cp:
        agent = build_agent(cp)
        cfg = {"configurable": {"thread_id": "probe"}, "tags": ["tier_req:T5"]}
        async for ev in agent.astream({"messages": "List three primary colors."}, cfg):
            print("EVENT:", list(ev.keys()) if isinstance(ev, dict) else type(ev))
asyncio.run(main())
PY
```
Accept when: it streams events, the router logs a `tier5` planner call (and `auto`
worker calls), and a plan + final answer come back. **Record the EVENT key shapes**
— `event_adapter.py` is written against them.

Commit the frozen `requirements.txt`.

---

## Remaining phases (in order — each gates the next)

### P3 — Event adapter + persistence (THE REAL WORK; budget for it)
Write `backend/observability/event_adapter.py`. Translate the deepagents stream
into the two things the UI + DB already speak: **CloudEvents over the WebSocket**
and **Step/Attempt rows**. The UI (`TaskGraph`, `WorkerGrid`) then lights up with
no rewrite.

Mapping (from the plan):
| deepagents event | App action |
| --- | --- |
| plan / `write_todos` | create Run + Steps; emit graph-created CloudEvent |
| subagent spawn | mark Step started; emit WorkerCard event |
| tool call | tool-call audit row; emit tool event |
| LLM call end | `RouteCaptureHandler` writes tier/cost onto the current Step/Attempt |
| subagent return | commit Step/Attempt with output |
| synthesis | produce `DocumentResult` |
| run done | finalize, run `cost.rollup_run`, kick async eval, broadcast |

Two joins: `thread_id == run_id`; tag each subagent invocation with
`tier_req:<tier>` **and** the owning `step_id`. Keep checkpointer and app DB
separate — UI reads the app DB only (see caveats: subagents have no own
checkpoint, #573, so capture from the stream live, not from checkpointer state).
- [ ] Reuse existing CloudEvents envelope (`schemas/models.py` `to_cloudevent`,
  `EventType`/`CE_TYPE`) and persistence facade (`repositories/persistence.py`).
- [ ] Sink for `RouteCaptureHandler` writes tier/cost onto `StepAttempt`.
- **Accept:** `LiveRunPage` shows a run progressing on the existing TaskGraph/WorkerGrid.

### P2 — Excise the old engine + wire `main.py`
Only after P1+P3 run. Repoint `run_swarm`/`launch_run` in `main.py` to build a
deepagents agent (`core.build_agent`), open the `AsyncSqliteSaver` in the FastAPI
lifespan, stream through `event_adapter.handle(...)`. Keep `launch_run` signature,
app/lifespan/scheduler wiring.
- [ ] `main.py` pipeline → `core.py` + `event_adapter`.
- [ ] MCP tools → deepagents via `langchain-mcp-adapters` (`protocols/mcp_servers.py`);
  resolve `{nickname: tool}` and pass to `profiles.build_subagent_profiles`.
- [ ] Then DELETE the GO list (below).
- **Accept:** a full run completes via deepagents; no `swarm_graph` involved.

**GO — delete (LAST, after the above is green):**
- [ ] `backend/graph/swarm_graph.py`
- [ ] `backend/agents/orchestrator.py`
- [ ] `backend/agents/planner.py`
- [ ] `backend/agents/worker.py`
- [ ] `backend/agents/reducer.py`
- [ ] `backend/inference/client.py` (constants already ported into `model.py`)
- [ ] `tests/test_swarm_graph.py` and the now-dead `tests/test_{planner,escalation,gate,retrieval,telemetry_and_tiers}.py`

### P4 — Tier + cost story (mostly done; finish the surfacing)
- [x] `cost.py`, tier/cost DB fields, Prometheus metrics (done in `78e548b`).
- [ ] `routers/runs.py`: `GET /runs/{id}/cost` (rollup from StepAttempt rows). **Additive — safe to do anytime.**
- [ ] `schemas/api.py` + `schemas/models.py`: tier/cost fields on Run/Step DTOs. **Additive.**
- [ ] Frontend `WorkerCard.tsx` (routed tier per agent) + `MetricsHUD.tsx` (savings figure); `types/swarm.ts`, `store/swarmStore.ts`, `hooks/useSwarmSocket.ts` carry tier/cost + new event shapes. **Additive.**
- **Accept:** a run surfaces a decomposed-vs-T5 savings number in the UI.

### P5 — Scheduler rebind
- [ ] `scheduling/scheduler.py`: launch target fires a deepagents run (fresh
  `thread_id == run_id`); logic otherwise unchanged.
- [ ] `repositories/jobs.py`: confirm `next_fire_at` via `croniter` —
  **already true** (`compute_next_fire` uses croniter); just verify.
- **Accept:** a cron job fires a deepagents run, honors overlap policy, lands in history.

### P6 — Eval, validation, HITL
- [ ] `agents/evaluator.py` + `evals/runner.py`: rewire LLM calls to the tier client
  (`mf.auto()`, or pin for consistency). Keep evaluator as the async post-run eval.
- [ ] `agents/validator.py`: **defer** (per-step validation is awkward in the harness;
  reattach as a post-subagent check later, or skip for the lean migration).
- [ ] Plan-approval interrupt (main-agent `interrupt_on`) + sensitive-tool gate
  (`code_exec`/`ticket_create`); resume with `Command(resume=...)`, `thread_id==run_id`.
  NOTE: subagent edit/reject interrupts are broken (#554) — keep gates at the MAIN agent.
- **Accept:** post-run eval recorded; plan approval pauses and resumes.

### P7 — Tests, scripts, demo polish
- [ ] Rewrite `tests/` against the deepagents core + tier contract (keep `test_cost.py`).
- [ ] Repoint `scripts/{smoke_test,dashboard,inspect_run,test_run}.py` at the new
  pipeline; dashboard can show tiers.
- [ ] Optional `agents/pinned_plans.py` from `decomposition_templates.yaml` for
  repeatable buyer demos.
- **Accept:** smoke test green; a canned objective yields a stable plan.

---

## UPDATE items not yet touched (reference)
- [ ] `config/endpoints.yaml` → point at `:8900`, remove specialty model names.
- [ ] `agents/tts.py` → keep only if TTS is in demo scope; else shelve.
- [ ] `config/decomposition_templates.yaml` → optional pinned-plan seeds (or shelve).
- [ ] `env.example` → remove the legacy `*_MODEL`/`LLM_TIER_TOKEN` block once
  `inference/client.py` is deleted (new ADL vars already added alongside).

## STAY — keep as-is (do not touch)
`db/base.py`, `db/ids.py`, `repositories/*`, `scheduling/scheduler.py` (logic),
`routers/*` (one add), `security/secrets.py`, `protocols/a2a_cards.py`,
`observability/{langfuse_client,trace}.py`, `evals/rubrics.py`,
`mcp_servers/*` (consumed via langchain-mcp-adapters), `frontend/**` (targeted
adds only), `config/prometheus.yml`.

---

## Caveats / gotchas
- **PEP 668** on the box: always use the `~/.venv-adl` venv for host Python, or run
  inside the Docker image. `--break-system-packages` pollutes system Python — avoid.
- **deepagents 0.6.10 bugs:** subagent edit/reject interrupts broken (#554) → HITL at
  MAIN agent only; subagents have no own checkpoint (#573) → event adapter must capture
  subagent activity from the **stream live**, never reconstruct from checkpointer state.
- **Planner tier governs synthesis** in deepagents (same main agent). `ADL_SYNTHESIS_TIER`
  only bites if synthesis is split into a dedicated subagent. Decide per the reference.
- **SQLite schema migration = delete the `.db` file.** `create_all` won't ALTER existing
  tables, so the new tier/cost columns need a fresh `orchestrator.db` (delete it; the app
  recreates on boot). Checkpointer DB (`CHECKPOINT_DB`) is a SEPARATE file, self-managed.
- **Gateway contract is fixed:** `model` = `auto`/`tier1..tier5` only (real model id →
  400); decision on `x-vsr-selected-*` headers (absent on cache hit → body model);
  `max_completion_tokens`; `SR_AUTH_MODE`; streaming unsupported.
- **Two stores, two jobs:** checkpointer replays live state; app DB serves history + UI.
  Never point the UI at checkpointer tables.

## Env (already in env.example)
`ROUTER_BASE` (=:8900), `ROUTER_BASE_URL`, `ADL_PLANNER_TIER` (T5),
`ADL_SYNTHESIS_TIER`, `ADL_WORKER_TIER` (auto), `ADL_MAX_COMPLETION_TOKENS`,
`SR_AUTH_MODE` (+ `SR_AUTH_EMAIL`/`SR_PROXY_SECRET` when proxy), `CHECKPOINT_DB`,
`DATABASE_URL` (app DB, unchanged), `TIER_COST_T1..T5` (illustrative).
