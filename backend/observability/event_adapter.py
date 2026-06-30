"""
backend/observability/event_adapter.py

Bridges the deepagents (LangGraph) event stream into the app's two existing
surfaces: the WebSocket CloudEvents feed (SwarmEvent) and the SQLite system of
record (Step / StepAttempt / Validation rows + the run cost rollup).

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
to a synthetic 'orchestrator' step so their (large) cost is captured too.

L0 mechanical validation runs on each subagent result as it lands (zero tokens),
emitting validator_* events and a Validation row. L1/L2 judging is Stage 3/5.
"""
from __future__ import annotations

import os
import time

from backend.schemas.models import EventType, SwarmEvent
from backend.observability.callbacks import to_internal_tier
from backend.observability.cost import rollup_run
from backend.observability.validation_l0 import validate_l0
from backend.repositories import persistence as db

_ORCH_STEP = "orchestrator"  # synthetic step holding planner + synthesis calls


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
                 planner_tier: str | None = None):
        self.run_id = run_id
        self.broadcast = broadcast
        self.db = persistence
        self.planner_tier = (planner_tier or os.environ.get("ADL_PLANNER_TIER", "T5")).upper()

        self.calls: list[dict] = []                 # every model call, for the cost rollup
        self.steps: dict[str, dict] = {}            # task_call_id -> {step_key, role, attempts}
        self._open_unbound: list[str] = []          # task_call_ids without a namespace yet
        self._ns_to_tcid: dict[tuple, str] = {}     # subagent namespace -> task_call_id
        self._delegation_n = 0
        self._orch_attempts = 0
        self._synthesis_emitted = False
        self.final_answer: str | None = None
        self.events: list[SwarmEvent] = []

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self, query: str) -> None:
        await self.db.create_step(self.run_id, step_key=_ORCH_STEP, type="orchestrate",
                                  objective="planner + synthesis", status="running")
        await self._emit(EventType.run_started, {"query": query})

    async def finalize(self, status: str = "completed") -> dict:
        cost = rollup_run(self.calls)
        await self.db.set_step_status(self.run_id, _ORCH_STEP, "completed",
                                      total_attempts=self._orch_attempts)
        metrics = {"task_count": self._delegation_n, **cost.as_dict()}
        await self.db.finalize_run(
            self.run_id,
            document_result={"final_answer": self.final_answer or ""},
            metrics=metrics, status=status,
            total_cost=cost.total_cost, baseline_cost=cost.baseline_cost,
            savings_pct=cost.savings_pct,
        )
        await self._emit(EventType.run_completed,
                         {"final_answer": self.final_answer or "",
                          "task_count": self._delegation_n})
        await self._emit(EventType.run_metrics, cost.as_dict())
        return {"cost": cost.as_dict(), "task_count": self._delegation_n,
                "final_answer": self.final_answer}

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
        role = (tc.get("args") or {}).get("subagent_type") or "general-purpose"
        desc = (tc.get("args") or {}).get("description") or ""
        step_key = f"{role}-{self._delegation_n}"[:32]
        tcid = tc.get("id")
        self.steps[tcid] = {"step_key": step_key, "role": role, "attempts": 0}
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
        result_text = m.content if isinstance(m.content, str) else str(m.content)

        await self._emit(EventType.validator_started, {"task_id": step_key})
        verdict = validate_l0(result_text, role=role)
        await self.db.record_validation(
            self.run_id, step_key, level="mechanical", verdict=verdict["verdict"],
            score=verdict["score"], detail={"checks": verdict["checks"],
                                            "summary": verdict["detail"]},
        )
        if verdict["verdict"] == "fail":
            await self.db.set_step_status(self.run_id, step_key, "failed",
                                          result={"text": result_text},
                                          total_attempts=info["attempts"])
            await self._emit(EventType.validator_rejected,
                             {"task_id": step_key, "verdict": verdict})
            await self._emit(EventType.task_failed,
                             {"task_id": step_key, "reason": verdict["detail"]})
        else:
            await self.db.set_step_status(self.run_id, step_key, "completed",
                                          result={"text": result_text},
                                          total_attempts=info["attempts"])
            await self._emit(EventType.validator_approved,
                             {"task_id": step_key, "verdict": verdict})
            await self._emit(EventType.task_completed,
                             {"task_id": step_key, "result": result_text,
                              "model_used": "auto"})

    # ── subagent (worker) calls and tools ────────────────────────────────────
    async def _handle_subagent(self, ns: tuple, m) -> None:
        tcid = self._bind_namespace(ns)
        if tcid is None:
            return
        info = self.steps.get(tcid)
        if info is None:
            return
        if type(m).__name__ == "AIMessage":
            tin, tout = _usage(m)
            if not (tout or tin):
                return                            # not a real model call
            info["attempts"] += 1
            tier = _tier_observed(m)
            await self.db.record_attempt(
                self.run_id, info["step_key"], attempt_no=info["attempts"],
                status="completed", tokens_in=tin, tokens_out=tout,
                tier_requested="auto", tier_observed=tier,
                category=_category(m), cache_hit=_cache_hit(m),
            )
            self.calls.append({"tokens_out": tout, "tier_observed": tier,
                               "cache_hit": _cache_hit(m)})

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


async def run_with_adapter(agent, query: str, run_id: str, *, broadcast=None,
                           persistence=db, planner_tier: str | None = None,
                           recursion_limit: int = 80) -> dict:
    """Drive one deepagents run through the adapter end to end.

    Streams the compiled agent with subgraphs=True so subagent-internal calls
    (and their routed tiers) are visible, feeding every event to the adapter.
    Returns the adapter's run summary. The caller owns run creation/teardown.
    """
    adapter = EventAdapter(run_id, broadcast, persistence=persistence,
                           planner_tier=planner_tier)
    await adapter.start(query)
    config = {"configurable": {"thread_id": run_id},
              "tags": [f"tier_req:{adapter.planner_tier}"],
              "recursion_limit": recursion_limit}
    status = "completed"
    t0 = time.time()
    try:
        async for ns, mode, chunk in agent.astream(
            {"messages": query}, config, stream_mode=["updates"], subgraphs=True
        ):
            await adapter.handle(ns, mode, chunk)
    except Exception as exc:  # noqa: BLE001 — surface as a failed run, don't crash caller
        status = "failed"
        await adapter._emit(EventType.error, {"error": f"{type(exc).__name__}: {exc}"})
    summary = await adapter.finalize(status=status)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    return summary
