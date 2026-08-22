"""
backend/observability/event_adapter.py

Bridges the deepagents (LangGraph) event stream into the app's two existing
surfaces: the WebSocket CloudEvents feed (SwarmEvent) and the SQLite system of
record (Step / StepAttempt / Validation rows + the run routing rollup).

Why a stream adapter and not the checkpointer: a subagent has its own context and
its tool history can be truncated in checkpoint state (deepagents #573), so the
app DB must be written from the stream AS IT HAPPENS, not reconstructed afterward.

Stream shape (verified against deepagents 0.6.10 via astream(subgraphs=True,
stream_mode=["updates"])):

  NS=()         model  AIMessage  task(<role>)     planner delegates (and a planner call)
  NS=(tools:X)  model  AIMessage  (worker calls)   the subagent's own model calls, with the
                                                    tier the ROUTER picked for that subtask
  NS=(tools:X)  tools  ToolMessage                  the subagent's tool calls
  NS=()         tools  ToolMessage name=task        the delegation closes; content = result
  NS=()         model  AIMessage  (no tool_calls)   synthesis: the main agent composes the answer

Correlation: a delegation is keyed by its `task` tool_call id (the closing
ToolMessage carries the same id). Subagent namespaces are bound to the open
delegation in dispatch order — deepagents runs subagents sequentially, so exactly
one delegation is open at a time. The planner/synthesis calls (NS=()) are pinned
to a synthetic 'orchestrator' step so their (large) token spend is captured too.

L0 mechanical validation runs on each subagent result as it lands (zero tokens),
emitting validator_* events and a Validation row. L1/L2 judging is Stage 3/5.
"""
from __future__ import annotations

import logging
import os
import time

from backend.schemas.models import EventType, SwarmEvent
from backend.observability.callbacks import to_internal_tier
from backend.observability.routing import rollup_routing
from backend.observability.validation_l0 import validate_l0
from backend.repositories import persistence as db

_ORCH_STEP = "orchestrator"  # synthetic step holding planner + synthesis calls


class BudgetExceeded(Exception):
    """Raised when a run breaches a budget ceiling. Caught by run_with_adapter,
    which stops the stream cleanly and lets synthesis work from partial results
    (the deepagents graph is abandoned, not crashed)."""

    def __init__(self, kind: str, used, limit):
        self.kind, self.used, self.limit = kind, used, limit
        super().__init__(f"budget '{kind}' exceeded: {used} > {limit}")


def _budget_from_env() -> dict:
    """Per-run budget ceilings (plan §4.4). 0/unset == unlimited for that dimension."""
    # Defaults are SAFETY CEILINGS (catch a runaway loop), not tight operating limits.
    # They must clear a normal run: max_tool_hops is per-agent and a worker spends several
    # hops on deepagents' built-in tools (write_todos, filesystem) before real work, and
    # max_subagents is per-run and must leave room for validation re-dispatches. Earlier
    # defaults (5 / 6) tripped on ordinary runs. 0 == unlimited for that dimension.
    return {
        "max_subagents": int(os.environ.get("ADL_MAX_SUBAGENTS", "16") or 0),
        "max_tool_hops": int(os.environ.get("ADL_MAX_TOOL_HOPS", "30") or 0),  # per agent
        "max_total_tokens": int(os.environ.get("ADL_MAX_TOTAL_TOKENS", "0") or 0),
    }


def _tier_observed(m) -> str | None:
    meta = getattr(m, "response_metadata", {}) or {}
    headers = {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}
    raw = headers.get("x-vsr-selected-model") or meta.get("model_name") or meta.get("model")
    return to_internal_tier(raw) if raw else None


def _cache_hit(m) -> bool:
    meta = getattr(m, "response_metadata", {}) or {}
    headers = {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}
    return "x-vsr-selected-model" not in headers  # header absent on a cache hit


def _category(m) -> str | None:
    meta = getattr(m, "response_metadata", {}) or {}
    headers = {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}
    return headers.get("x-vsr-selected-category")


def _usage(m) -> tuple[int, int]:
    u = getattr(m, "usage_metadata", None) or {}
    return int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)


def _task_calls(m) -> list[dict]:
    return [tc for tc in (getattr(m, "tool_calls", None) or []) if tc.get("name") == "task"]


class EventAdapter:
    """Consume deepagents stream events for one run; persist + broadcast.

    broadcast: optional async callable (run_id, SwarmEvent) -> awaitable. When
    None (headless tests) events are collected in `self.events` instead.
    persistence: the db facade (injectable for tests); defaults to the real one.
    """

    def __init__(self, run_id: str, broadcast=None, *, persistence=db,
                 planner_tier: str | None = None, judge=None, redispatch=None,
                 validation_cfg: dict | None = None, synthesis_grader=None,
                 partial_synthesizer=None, budget: dict | None = None):
        self.run_id = run_id
        self.broadcast = broadcast
        self.db = persistence
        self.planner_tier = (planner_tier or os.environ.get("ADL_PLANNER_TIER", "T5")).upper()

        # L1/L2 judging is opt-in: when no judge is injected, only L0 mechanical
        # runs (Stage 2 behavior). The real run path passes make_judge(mf) /
        # make_redispatch(mf) from validation_judge.py. validation_cfg maps a role
        # to its {level, tier, rubric, retries}; loaded lazily so headless tests
        # that don't exercise judging need no config.
        self._judge = judge
        self._redispatch = redispatch
        if validation_cfg is None and judge is not None:
            from backend.agents.profiles import validation_config
            validation_cfg = validation_config()
        self._validation_cfg = validation_cfg or {}
        self.validation_tokens = 0                  # validator spend, separate from generation

        # L2 frontier synthesis grader (opt-in): graded once in finalize against the
        # objective + the collected subtask results — the most important validator
        # (directive). Budgets stop a run cleanly with partial synthesis on breach.
        self._synthesis_grader = synthesis_grader
        # Fallback synthesizer: composes a final answer from partial results when the
        # graph was abandoned (budget stop) before the main agent synthesized.
        self._partial_synthesizer = partial_synthesizer
        self.budget = budget if budget is not None else _budget_from_env()
        self.total_tokens = 0
        self.budget_exceeded: dict | None = None

        self.calls: list[dict] = []                 # every model call, for the routing rollup
        self.steps: dict[str, dict] = {}            # task_call_id -> {step_key, role, attempts}
        self._open_unbound: list[str] = []          # task_call_ids without a namespace yet
        self._ns_to_tcid: dict[tuple, str] = {}     # subagent namespace -> task_call_id
        self._delegation_n = 0
        self._orch_attempts = 0
        self._synthesis_emitted = False
        self.query = ""
        self.final_answer: str | None = None
        self._results: list[dict] = []              # per-delegation result, for synthesis grading
        self.events: list[SwarmEvent] = []

    # ── budget accounting ──────────────────────────────────────────────────────
    def _account_tokens(self, tin: int, tout: int) -> None:
        self.total_tokens += int(tin or 0) + int(tout or 0)
        cap = self.budget.get("max_total_tokens", 0)
        if cap and self.total_tokens > cap:
            raise BudgetExceeded("max_total_tokens", self.total_tokens, cap)

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self, query: str) -> None:
        self.query = query
        await self.db.create_step(self.run_id, step_key=_ORCH_STEP, type="orchestrate",
                                  objective="planner + synthesis", status="running")
        await self._emit(EventType.run_started, {"query": query})

    async def _grade_synthesis(self) -> None:
        """Run the L2 frontier grader on the final answer (directive §Synthesis)."""
        if not self._synthesis_grader or not (self.final_answer or "").strip():
            return
        await self._emit(EventType.validator_started, {"task_id": _ORCH_STEP})
        gr = await self._synthesis_grader(self.query, self.final_answer, self._results)
        self.validation_tokens += int(gr.get("tokens_in") or 0) + int(gr.get("tokens_out") or 0)
        await self.db.record_validation(
            self.run_id, _ORCH_STEP, level=gr.get("level", "frontier"),
            verdict=gr["verdict"], score=gr.get("score"),
            validator_tier=gr.get("validator_tier"), rubric_id=gr.get("rubric_id"),
            escalated=True, detail={"critique": gr.get("critique", "")},
            tokens_in=gr.get("tokens_in"), tokens_out=gr.get("tokens_out"),
        )
        event = (EventType.validator_approved if gr["verdict"] == "pass"
                 else EventType.validator_rejected)
        await self._emit(event, {"task_id": _ORCH_STEP, "verdict_kind": gr["verdict"],
                                 "critique": gr.get("critique", "")})

    async def finalize(self, status: str = "completed",
                       error: str | None = None) -> dict:
        # Partial-synthesis fallback: if the graph was abandoned (budget stop) before the
        # main agent composed an answer, build one from the collected subtask results so
        # the run delivers something instead of an empty answer. Tokens roll into the
        # rollup but skip the budget check (already at the ceiling — finalize must close).
        if (self._partial_synthesizer is not None and self._results
                and not (self.final_answer or "").strip()):
            try:
                ps = await self._partial_synthesizer(self.query, self._results)
                self.final_answer = ps.get("final_answer") or self.final_answer
                tin, tout = int(ps.get("tokens_in") or 0), int(ps.get("tokens_out") or 0)
                if tin or tout:
                    self.total_tokens += tin + tout
                    self.calls.append({"tokens_out": tout,
                                       "tier_observed": ps.get("tier_observed"),
                                       "cache_hit": False})
                await self._emit(EventType.synthesis_started, {"partial": True})
            except Exception as exc:  # noqa: BLE001 — never block finalize
                await self._emit(EventType.error, {"error": f"partial synthesis: {exc}"})

        # L2 synthesis grade runs before the run closes (even on a budget stop, so a
        # truncated answer is still graded). A grader failure must not block finalize.
        try:
            await self._grade_synthesis()
        except Exception as exc:  # noqa: BLE001
            await self._emit(EventType.error, {"error": f"synthesis grader: {exc}"})

        routing = rollup_routing(self.calls)
        await self.db.set_step_status(self.run_id, _ORCH_STEP, "completed",
                                      total_attempts=self._orch_attempts)
        metrics = {"task_count": self._delegation_n,
                   "validation_tokens": self.validation_tokens,
                   "total_tokens": self.total_tokens, **routing.as_dict()}
        if self.budget_exceeded:
            metrics["budget_exceeded"] = self.budget_exceeded
        await self.db.finalize_run(
            self.run_id,
            document_result={"final_answer": self.final_answer or ""},
            metrics=metrics, status=status, error=error,
        )
        await self._emit(EventType.run_completed,
                         {"final_answer": self.final_answer or "",
                          "task_count": self._delegation_n,
                          "budget_exceeded": self.budget_exceeded})
        await self._emit(EventType.run_metrics, {
            **routing.as_dict(),
            "task_count": self._delegation_n,
            "total_tokens": self.total_tokens,
            "validation_tokens": self.validation_tokens,
        })
        return {"routing": routing.as_dict(), "task_count": self._delegation_n,
                "final_answer": self.final_answer, "budget_exceeded": self.budget_exceeded}

    # ── main entry: one stream event (namespace, mode, chunk) ────────────────
    async def handle(self, ns: tuple, mode: str, chunk: dict) -> None:
        for _node, upd in (chunk or {}).items():
            msgs = (upd or {}).get("messages", []) if isinstance(upd, dict) else []
            for m in msgs:
                if ns == ():
                    await self._handle_parent(m)
                else:
                    await self._handle_subagent(ns, m)

    # ── parent (main agent): planner, delegation close, synthesis ────────────
    async def _handle_parent(self, m) -> None:
        kind = type(m).__name__
        if kind == "AIMessage":
            tin, tout = _usage(m)
            if tout or tin:                      # a real planner/synthesis model call
                self._orch_attempts += 1
                tier = _tier_observed(m)
                await self.db.record_attempt(
                    self.run_id, _ORCH_STEP, attempt_no=self._orch_attempts,
                    status="completed", tokens_in=tin, tokens_out=tout,
                    tier_requested=self.planner_tier, tier_observed=tier,
                    category=_category(m), cache_hit=_cache_hit(m),
                )
                self.calls.append({"tokens_out": tout, "tier_observed": tier,
                                   "cache_hit": _cache_hit(m)})
                self._account_tokens(tin, tout)
            tcalls = _task_calls(m)
            if tcalls:
                for tc in tcalls:
                    await self._open_delegation(tc)
            elif (getattr(m, "content", "") or "").strip():
                # No delegation + content = the synthesis turn.
                self.final_answer = m.content
                if not self._synthesis_emitted and self._delegation_n:
                    self._synthesis_emitted = True
                    await self._emit(EventType.synthesis_started, {})
        elif kind == "ToolMessage" and getattr(m, "name", None) == "task":
            await self._close_delegation(m)

    async def _open_delegation(self, tc: dict) -> None:
        self._delegation_n += 1
        cap = self.budget.get("max_subagents", 0)
        if cap and self._delegation_n > cap:
            raise BudgetExceeded("max_subagents", self._delegation_n, cap)
        role = (tc.get("args") or {}).get("subagent_type") or "general-purpose"
        desc = (tc.get("args") or {}).get("description") or ""
        step_key = f"{role}-{self._delegation_n}"[:32]
        tcid = tc.get("id")
        self.steps[tcid] = {"step_key": step_key, "role": role, "attempts": 0,
                            "desc": desc, "tool_hops": 0,
                            # routing telemetry for this worker (surfaced on task_completed)
                            "tier_observed": None, "category": None,
                            "cache_hits": 0, "tokens_out": 0}
        self._open_unbound.append(tcid)
        await self.db.create_step(self.run_id, step_key=step_key, type=role,
                                  objective=desc, status="running")
        await self._emit(EventType.task_started,
                         {"task_id": step_key, "description": desc,
                          "type": role, "model": "auto"})

    async def _close_delegation(self, m) -> None:
        info = self.steps.get(getattr(m, "tool_call_id", None))
        if info is None:
            return
        step_key, role = info["step_key"], info["role"]
        subtask = info.get("desc", "")
        result_text = m.content if isinstance(m.content, str) else str(m.content)

        await self._emit(EventType.validator_started, {"task_id": step_key})

        cfg = self._validation_cfg.get(role, {})
        level = cfg.get("level", "mechanical")
        max_retries = int(cfg.get("retries", 0))
        retries_used = 0
        verdict_kind = "pass"
        l0_failed = False
        critique = ""

        # Validate → (optionally) re-dispatch → re-validate, bounded by max_retries.
        # L0 mechanical runs every pass (free); the judge runs only when this role is
        # configured for judge/frontier AND a judge was injected. A flaky validator
        # cannot loop forever — the retry cap is the latency guard (directive).
        while True:
            l0 = validate_l0(result_text, role=role)
            l0_failed = l0["verdict"] == "fail"
            await self.db.record_validation(
                self.run_id, step_key, level="mechanical", verdict=l0["verdict"],
                score=l0["score"], retries_used=retries_used,
                detail={"checks": l0["checks"], "summary": l0["detail"]},
            )
            if l0["verdict"] == "fail":
                verdict_kind, critique = "fail", l0["detail"]
            elif level in ("judge", "frontier") and self._judge is not None:
                jr = await self._judge(subtask, result_text, role, cfg)
                self.validation_tokens += (int(jr.get("tokens_in") or 0)
                                           + int(jr.get("tokens_out") or 0))
                await self.db.record_validation(
                    self.run_id, step_key, level=jr["level"], verdict=jr["verdict"],
                    score=jr["score"], validator_tier=jr.get("validator_tier"),
                    rubric_id=jr.get("rubric_id"), retries_used=retries_used,
                    escalated=(jr["level"] == "frontier"),
                    detail={"critique": jr.get("critique", "")},
                    tokens_in=jr.get("tokens_in"), tokens_out=jr.get("tokens_out"),
                )
                verdict_kind, critique = jr["verdict"], jr.get("critique", "")
            else:
                verdict_kind, critique = l0["verdict"], l0["detail"]

            if verdict_kind == "pass" or retries_used >= max_retries or self._redispatch is None:
                break

            # Bounded re-dispatch of the failed/degraded subtask with the critique.
            retries_used += 1
            await self._emit(EventType.worker_retrying,
                             {"task_id": step_key, "attempt": retries_used,
                              "hint": critique})
            rd = await self._redispatch(role, subtask, critique)
            result_text = rd.get("result", "") or ""
            info["attempts"] += 1
            tout = int(rd.get("tokens_out") or 0)
            info["tier_observed"] = rd.get("tier_observed") or info["tier_observed"]
            info["cache_hits"] += 1 if rd.get("cache_hit") else 0
            info["tokens_out"] += tout
            await self.db.record_attempt(
                self.run_id, step_key, attempt_no=info["attempts"],
                status="completed", tokens_in=int(rd.get("tokens_in") or 0),
                tokens_out=tout, tier_requested="auto",
                tier_observed=rd.get("tier_observed"), cache_hit=bool(rd.get("cache_hit")),
            )
            self.calls.append({"tokens_out": tout, "tier_observed": rd.get("tier_observed"),
                               "cache_hit": bool(rd.get("cache_hit"))})
            self._account_tokens(int(rd.get("tokens_in") or 0), tout)

        # Terminal mapping (directive): a genuinely unusable result (L0 empty) hard
        # -fails the step; a judge-unsatisfied but non-empty result after the retry
        # cap is DEGRADED (kept and surfaced to synthesis), never silently passed.
        if l0_failed:
            terminal = "fail"
        elif verdict_kind == "pass":
            terminal = "pass"
        else:
            terminal = "degraded"
        await self._finish_step(step_key, terminal, result_text, info["attempts"],
                                retries_used, critique, info)

    def _routing_of(self, info: dict) -> dict:
        """Per-worker routing telemetry for the WS events (the router's decision)."""
        return {"tier_observed": info.get("tier_observed"),
                "category": info.get("category"),
                "cache_hits": info.get("cache_hits", 0),
                "tokens_out": info.get("tokens_out", 0),
                "tool_hops": info.get("tool_hops", 0)}

    async def _finish_step(self, step_key, terminal, result_text, attempts,
                           retries_used, critique, info) -> None:
        """Record terminal step state + emit events for a validated delegation.

        pass     → completed, validator_approved.
        degraded → completed (result still usable) but validator_rejected so the UI
                   flags it amber; if retries were spent, worker_rejected_final too.
        fail     → failed, validator_rejected + task_failed.
        """
        if terminal == "fail":
            await self.db.set_step_status(self.run_id, step_key, "failed",
                                          result={"text": result_text},
                                          total_attempts=attempts)
            await self._emit(EventType.validator_rejected,
                             {"task_id": step_key, "verdict_kind": "fail", "critique": critique})
            await self._emit(EventType.task_failed,
                             {"task_id": step_key, "reason": critique,
                              "attempts": attempts, **self._routing_of(info)})
            return

        await self.db.set_step_status(self.run_id, step_key, "completed",
                                      result={"text": result_text},
                                      total_attempts=attempts)
        # Keep the usable result for the synthesis grader's cross-check.
        self._results.append({"step_key": step_key, "terminal": terminal,
                              "result": result_text})
        if terminal == "degraded":
            await self._emit(EventType.validator_rejected,
                             {"task_id": step_key, "verdict_kind": "degraded",
                              "critique": critique})
            if retries_used:
                await self._emit(EventType.worker_rejected_final,
                                 {"task_id": step_key, "retries_used": retries_used,
                                  "critique": critique})
        else:
            await self._emit(EventType.validator_approved, {"task_id": step_key})
        await self._emit(EventType.task_completed,
                         {"task_id": step_key, "result": result_text,
                          "verdict": terminal, "attempts": attempts,
                          "retries_used": retries_used, **self._routing_of(info)})

    # ── subagent (worker) calls and tools ────────────────────────────────────
    async def _handle_subagent(self, ns: tuple, m) -> None:
        tcid = self._bind_namespace(ns)
        if tcid is None:
            return
        info = self.steps.get(tcid)
        if info is None:
            return
        kind = type(m).__name__
        if kind == "ToolMessage":
            # A worker tool call (one tool hop). Bound per agent (plan §4.4).
            info["tool_hops"] += 1
            cap = self.budget.get("max_tool_hops", 0)
            if cap and info["tool_hops"] > cap:
                raise BudgetExceeded("max_tool_hops", info["tool_hops"], cap)
            return
        if kind == "AIMessage":
            tin, tout = _usage(m)
            if not (tout or tin):
                return                            # not a real model call
            info["attempts"] += 1
            tier = _tier_observed(m)
            category = _category(m)
            hit = _cache_hit(m)
            info["tier_observed"] = tier or info["tier_observed"]
            info["category"] = category or info["category"]
            info["cache_hits"] += 1 if hit else 0
            info["tokens_out"] += tout
            await self.db.record_attempt(
                self.run_id, info["step_key"], attempt_no=info["attempts"],
                status="completed", tokens_in=tin, tokens_out=tout,
                tier_requested="auto", tier_observed=tier,
                category=category, cache_hit=hit,
            )
            self.calls.append({"tokens_out": tout, "tier_observed": tier,
                               "cache_hit": hit})
            self._account_tokens(tin, tout)

    def _bind_namespace(self, ns: tuple) -> str | None:
        """Map a subagent namespace to its delegation's task_call_id. First-seen
        namespaces bind to the oldest still-unbound open delegation (sequential
        dispatch => one open at a time)."""
        top = ns[:1]                              # bind on the first-level namespace
        if top in self._ns_to_tcid:
            return self._ns_to_tcid[top]
        if not self._open_unbound:
            return None
        tcid = self._open_unbound.pop(0)
        self._ns_to_tcid[top] = tcid
        return tcid

    # ── emit helper ──────────────────────────────────────────────────────────
    async def _emit(self, event: EventType, payload: dict) -> None:
        ev = SwarmEvent(event=event, run_id=self.run_id, payload=payload)
        self.events.append(ev)
        if self.broadcast is not None:
            await self.broadcast(self.run_id, ev)


def _interrupt_payload(chunk) -> object | None:
    """Return the interrupt value if this stream chunk is a HITL interrupt, else None.
    langgraph surfaces a pending interrupt as a top-level `__interrupt__` key."""
    if isinstance(chunk, dict) and "__interrupt__" in chunk:
        return chunk["__interrupt__"]
    return None


def _plan_from_interrupt(payload) -> str | None:
    """Extract the submitted plan text from a HITL interrupt payload, so the UI can
    render the proposed task breakdown instead of a raw repr. The submit_plan gate's
    action_request args carry {"plan": "<numbered list>"} (core.build_submit_plan_tool)."""
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    for it in items:
        val = getattr(it, "value", it)
        if not isinstance(val, dict):
            continue
        for req in val.get("action_requests") or []:
            args = req.get("args") if isinstance(req, dict) else getattr(req, "args", None)
            if isinstance(args, dict) and args.get("plan"):
                return str(args["plan"])
    return None


def _decision_count(payload) -> int:
    """Number of action_requests carried by a HITL interrupt payload.

    langchain's HumanInTheLoopMiddleware resumes via `interrupt(request)["decisions"]`
    and requires exactly one decision per interrupted tool call, so the resume value
    must be a list of that length — a bare string raises `TypeError: string indices`."""
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    n = 0
    for it in items:
        val = getattr(it, "value", it)
        if isinstance(val, dict):
            n += len(val.get("action_requests") or [])
    return n or 1


async def run_with_adapter(agent, query: str, run_id: str, *, broadcast=None,
                           persistence=db, planner_tier: str | None = None,
                           judge=None, redispatch=None, validation_cfg: dict | None = None,
                           synthesis_grader=None, partial_synthesizer=None,
                           budget: dict | None = None,
                           approval=None, recursion_limit: int = 80) -> dict:
    """Drive one deepagents run through the adapter end to end.

    Streams the compiled agent with subgraphs=True so subagent-internal calls
    (and their routed tiers) are visible, feeding every event to the adapter.
    Pass judge/redispatch (from validation_judge.make_judge/make_redispatch) to
    enable L1/L2 graded validation + bounded retry; synthesis_grader for the L2
    output check; budget for the per-run ceilings.

    HITL: when the graph hits a plan-approval interrupt (core.build_interrupts), the
    `approval` coroutine is awaited for a decision and the graph resumes with
    Command(resume=decision) on the same thread_id. With no `approval` the run
    auto-approves so non-interactive runs are never blocked; a "reject" aborts.
    The live interrupt path requires a real planning run on the gateway to exercise.

    Returns the adapter's run summary. The caller owns run creation/teardown.
    """
    adapter = EventAdapter(run_id, broadcast, persistence=persistence,
                           planner_tier=planner_tier, judge=judge,
                           redispatch=redispatch, validation_cfg=validation_cfg,
                           synthesis_grader=synthesis_grader,
                           partial_synthesizer=partial_synthesizer, budget=budget)
    await adapter.start(query)
    config = {"configurable": {"thread_id": run_id},
              "tags": [f"tier_req:{adapter.planner_tier}"],
              "recursion_limit": recursion_limit}
    status = "completed"
    run_error: str | None = None
    t0 = time.time()
    stream_input: object = {"messages": query}
    try:
        while True:                                  # resume loop for HITL interrupts
            interrupted = False
            interrupt_payload = None
            async for ns, mode, chunk in agent.astream(
                stream_input, config, stream_mode=["updates"], subgraphs=True
            ):
                payload = _interrupt_payload(chunk)
                if payload is not None:
                    interrupted = True
                    interrupt_payload = payload
                    plan_text = _plan_from_interrupt(payload)
                    await adapter._emit(EventType.awaiting_approval,
                                        {"interrupt": str(payload)[:500],
                                         "plan": plan_text})
                    # Persist the proposed plan so a client arriving later (or the
                    # Activity page) can render it and approve — not just the live
                    # WS listener that caught the event.
                    if plan_text:
                        await persistence.save_task_graph(run_id, {"plan": plan_text})
                    await persistence.set_run_status(run_id, "awaiting_approval")
                    break
                await adapter.handle(ns, mode, chunk)
            if not interrupted:
                break
            decision = await approval() if approval is not None else "approve"
            if str(decision).lower() == "reject":
                status = "aborted"
                break
            await adapter._emit(EventType.run_resumed, {"decision": str(decision)})
            await persistence.set_run_status(run_id, "running")
            from langgraph.types import Command
            # HumanInTheLoopMiddleware expects interrupt(...)["decisions"]: one
            # {"type": <decision>} per interrupted tool call, not a bare string.
            decisions = [{"type": str(decision).lower()}
                         for _ in range(_decision_count(interrupt_payload))]
            stream_input = Command(resume={"decisions": decisions})
    except BudgetExceeded as be:
        # Clean stop: abandon the graph, keep partial results, let synthesis grade
        # whatever was produced. The run still completes (it is not a failure).
        adapter.budget_exceeded = {"kind": be.kind, "used": be.used, "limit": be.limit}
        await adapter._emit(EventType.error,
                            {"error": str(be), "budget_exceeded": adapter.budget_exceeded})
    except Exception as exc:  # noqa: BLE001 — surface as a failed run, don't crash caller
        status = "failed"
        run_error = f"{type(exc).__name__}: {exc}"
        logging.getLogger(__name__).exception("run %s failed in graph stream", run_id)
        await adapter._emit(EventType.error, {"error": run_error})
    summary = await adapter.finalize(status=status, error=run_error)
    summary["status"] = status
    summary["elapsed_s"] = round(time.time() - t0, 1)
    return summary
