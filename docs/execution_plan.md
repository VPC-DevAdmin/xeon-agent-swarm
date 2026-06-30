# Execution Plan — deepagents / ADL Migration

Working plan for migrating the hand-rolled LangGraph "swarm" orchestrator onto
**deepagents 0.6.10**, talking to the tier-router gateway (`:8900`) as an external
dependency. Target architecture: `docs/decomposition_layer_plan.md`. Verified API
shapes: `docs/deepagents_integration_reference.md`. Validation policy:
`docs/validation_directive.md` (amends Stages 2 and 5 below).

The handoff this plan continues lives in the body of commit `d393d73`.

---

## 1. Current state

**Landed (code present, not wired into the live pipeline):**

| Phase | Artifact | State |
| --- | --- | --- |
| P4 cost | `observability/cost.py`, `metrics.py`, `Run`/`StepAttempt` cost+tier columns, `TIER_COST_*` env | committed `78e548b`, tested (`tests/test_cost.py`) |
| P0 router rebind | `inference/model.py` — `ModelFactory` (tier selector, `x-vsr-*` capture, proxy auth, streaming off, cold-CPU timeout/retries) | committed `d393d73` |
| P1 decomposition | `agents/core.py` (`build_agent`), `agents/profiles.py` (from `config/worker_roles.yaml`) | committed `d393d73` |
| P4 capture | `observability/callbacks.py` — `RouteCaptureHandler` (header capture, cache-hit fallback, `tier_req:` tag) | committed `d393d73`, no sink wired |

**State change since the commit:** `backend/requirements.txt` is now a real `pip freeze`
(deepagents 0.6.10 + the langchain/langgraph stack present), not the loose "intent" set.
The dependency install every prior commit called blocked has now happened on the box.
This unblocks the P1 acceptance gate the handoff waits on.

**Not done (the gap to a working engine):**

- `main.py` still runs the **old swarm** (`orchestrator` / `worker` / `reducer` /
  `swarm_graph`). Nothing imports `agents.core`.
- **`event_adapter.py` does not exist** — the bridge from deepagents' typed stream →
  CloudEvents (WS) + `Step` / `StepAttempt` rows. Named first in the handoff.
- MCP tools (`mcp_servers/`) are **not** converted via `langchain-mcp-adapters`, so
  profiles currently receive **zero tools**.
- Cost rollup (`rollup_run`) is not called on run finalize.
- HITL gates (`INTERRUPTS` in `core.py`) and budget middleware are empty stubs.
- Tiered validation (per `validation_directive.md`) is unimplemented.

---

## 2. Resolved decisions

- **Validator/eval fork: KEEP, as a tier.** Validation runs on **every step**, implemented
  as L0 mechanical (zero-token) → L1 cheap judge (tier1/2) → L2 frontier (tier4/5),
  with level and tier declared per role. Synthesis gets an output-level grader. Full
  spec in `docs/validation_directive.md`. Retries are bounded so a flaky validator
  cannot become a latency cliff.
- **Concurrency:** deepagents delegation is LLM-driven and sequential by default. Do
  **not** force an explicit parallel DAG onto the harness (per the integration
  reference). Treat wall-clock overlap as a later, measured concern, not a Stage-1 goal.
- **Synthesis tier:** the main agent is planner **and** synthesizer, so `ADL_PLANNER_TIER`
  governs both. A cheaper/harder synthesis is a dedicated `synthesizer` subagent — adopt
  only if needed.
- **System of record:** the app SQLite DB, not the langgraph checkpointer. The checkpointer
  owns live/resume state only; subagent activity is captured from the stream as it happens.

---

## 3. Stages

Each stage has an acceptance gate. Do not advance until it passes. Validation is folded
into Stages 2–5 (do not retrofit it after a green run).

### Stage 1 — Prove the foundation on the box (P0 + P1 acceptance)

The gate the handoff names. Runs against the live gateway on the R470.

1. Confirm the freeze imports cleanly (`deepagents`, `langgraph.checkpoint.sqlite.aio`,
   `langchain_openai`, `langchain_mcp_adapters`); commit `requirements.txt`; remove the
   stray untracked `.env.bak`.
2. **P0 smoke:** `mf.auto()` and `mf.for_tier("T5")` round-trip through `:8900`; confirm the
   expected tiers appear in router logs and `x-vsr-*` headers surface in
   `response_metadata["headers"]`.
3. **P1 acceptance:** drive `build_agent` on a canned objective → assert a 2–5 subtask plan
   bound to profiles, zero hand-authored agents.

*Accept:* both pass on the R470.

### Stage 2 — Event adapter + L0 mechanical validation (P4)

The linchpin. Subscribe to deepagents' typed stream projections (messages, tool calls,
**subagents**) and map them to the existing surfaces:

- spawn / route / tool / return → `SwarmEvent.to_cloudevent()` WS envelopes **and**
  `Step` / `StepAttempt` rows.
- Wire `RouteCaptureHandler.sink` to write tier/tokens onto the current attempt.
- `thread_id == run_id`; tag each invocation `tier_req:<tier>` + owning `step_id`.
- **L0 mechanical validation** (per directive): as each subagent result lands, run the
  pure-function schema/required-field/regex/range checks ported from the old
  `validator.py`. Emit `validate_start` / `validate_result` WS events and write a
  `validation` record. Free, and it catches most repeating-workload failures from day one.

*Accept:* a run produces complete `agents` / `calls` / `tool_calls` / `events` /
`validation` rows + a correct cost table; the UI shows per-step validation status.

### Stage 3 — Tool wiring + L1 judge validation (P3)

- Convert `mcp_servers/` (web_search, code_exec, doc_retrieval) via
  `langchain-mcp-adapters` into a `{nickname: tool}` map; pass per-role grants
  (`profiles.py` / `grant_map()`) into the main agent and each subagent.
- Extend `config/worker_roles.yaml` with the per-role `validation` block
  (level / tier / rubric / retries).
- Add **L1 cheap-judge** validators via the gateway (`RubricMiddleware` if its 0.6.10 API
  fits, else a `ModelFactory.for_tier(validator_tier)` grader call) and the bounded
  retry-on-critique re-dispatch.

*Accept:* `researcher` can call `web_search` and others can't; `GET /v1/toolbox` (or the
existing toolbox view) lists grants; a judge validator escalates/degrades correctly within
its retry cap.

### Stage 4 — Wire `main.py` to `core.py` behind a flag

- New run path runs the deep agent + event adapter; old swarm stays reachable via
  `ADL_ENGINE=deepagents|swarm` until the new path is green.
- Call `rollup_run` on finalize; persist `total_cost` / `baseline_cost` / `savings_pct`,
  and validation cost **separately** from generation cost.

*Accept:* a full run drives end-to-end over WS with a live trace and a cost+validation
rollup; the old path still works under the flag.

### Stage 5 — Synthesis grader, HITL, budgets (P7)

- **L2 frontier synthesis grader** (per directive): output-level check on the final
  artifact against the objective and the subtask results — the most important validator
  in the pipeline.
- Plan-approval interrupt at the **main-agent** level (safe around deepagents issues #554 /
  #573); resume with `Command(resume=...)` on `thread_id == run_id`.
- Budget middleware: `max_subagents`, `max_tool_hops`, `max_validation_retries`,
  `max_total_tokens`. On breach, stop cleanly and let synthesis work from partial results.

*Accept:* a run pauses for approval and resumes/aborts; a budget stops a run cleanly with
partial synthesis; the synthesis grader flags a deliberately contradictory result.

### Stage 6 — Cutover

- Flip the default engine to deepagents; delete the old engine
  (`orchestrator` / `worker` / `reducer` / `swarm_graph`).
- `evaluator.py` and the eval rubrics stay in-tree (reused by the tiered path).
- Reconcile tests, update `README.md`.

*Accept:* the deepagents path is the only path; tests green; README reflects it.

---

## 4. Data model additions

Beyond the committed cost/tier columns on `Run` and `StepAttempt` (`78e548b`):

```
validation(step_id, level, validator_tier, rubric_id, verdict, score,
           retries_used, escalated, ts)
```

Roll up validation cost separately from generation cost.

---

## 5. Env additions

From `validation_directive.md` (alongside the existing `ADL_*` / `ROUTER_*` / `TIER_COST_*`):

```
ADL_VALIDATION_DEFAULT_LEVEL=judge        # mechanical | judge | frontier
ADL_DEFAULT_VALIDATOR_TIER=tier1
ADL_SYNTHESIS_VALIDATOR_TIER=tier4
ADL_MAX_VALIDATION_RETRIES=2
ADL_ENGINE=swarm                          # swarm | deepagents (flip at cutover)
```

---

## 6. Risks / open

- **deepagents API drift.** Verify `create_deep_agent`, `SubAgent` shape, `interrupt_on`,
  `RubricMiddleware`, and the `AsyncSqliteSaver` import path against the *installed* 0.6.10
  (`python -c "import deepagents, inspect; ..."`), not from memory.
- **#554 / #573.** Subagent edit/reject interrupts and subagent checkpoint persistence are
  buggy. Keep HITL at the main agent; capture subagent activity from the stream.
- **Concurrency is logical until proven physical.** Latency overlap is a measured claim
  needing two co-resident model servers — out of scope until Stage 4 is green.
- **Old SQLite files** need a delete to pick up the additive columns (consistent with the
  `create_all` model).
