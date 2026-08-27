"""
Graceful budget enforcement: budgets DECLINE excess work instead of killing
the workflow.

The event adapter's budget checks were built as malfunction detectors: see a
violation in the stream, abort the run. Live-model benchmarking showed the
malfunction they detect — a planner over-delegating in ~5% of units, a worker
over-calling its tool in ~3% — has a sane production response that whole-run
abortion is not. Real orchestrators decline an excess request and continue.

Two middlewares implement that, at the two stages where budgets bind:

  PlanBudgetGuard   main agent. An AIMessage whose delegation batch would push
                    the run past max_subagents is rejected WHOLE, before any
                    worker spawns: every `task` call in the batch returns a
                    rejection ToolMessage with replan feedback and no worker
                    runs, so the unit stays its declared size instead of being
                    silently clamped to a bigger one. Bounded: after
                    MAX_REPLANS rejected plans the next oversized batch raises
                    and the unit fails, which by then it deserves to.
  ToolBudgetGuard   workers. A tool call past max_tool_hops returns a
                    finalize-now notice instead of executing. Mid-worker,
                    minutes of work are already done, so kill-and-respawn is
                    the expensive remedy where the cheap one works.

Both are visible, never silent: rejections and exhaustions carry sentinel
prefixes the event adapter counts into run metrics, so a result can report
"4% of units required a replan" as a measured quality of the model.

Enforcement is stateless per run: counts are recomputed from the transcript in
the request state, because compiled agents are cached and shared across
concurrent runs, so instance state would leak between them.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

PLAN_REJECT_PREFIX = "[plan-rejected]"
TOOL_EXHAUSTED_PREFIX = "[tool-budget-exhausted]"
MAX_REPLANS = 2


def _executed_delegations(messages) -> int:
    """Delegations that actually ran: task ToolMessages minus rejections."""
    n = 0
    for m in messages:
        if (isinstance(m, ToolMessage) and getattr(m, "name", None) == "task"
                and not str(getattr(m, "content", "")).startswith(
                    PLAN_REJECT_PREFIX)):
            n += 1
    return n


def _rejected_plans(messages) -> int:
    """Whole plans rejected so far: rejection batches, counted once each."""
    n = 0
    prev_was_rejection = False
    for m in messages:
        is_rejection = (isinstance(m, ToolMessage)
                        and str(getattr(m, "content", "")).startswith(
                            PLAN_REJECT_PREFIX))
        if is_rejection and not prev_was_rejection:
            n += 1
        prev_was_rejection = is_rejection
    return n


def _batch_size(messages, tool_call_id: str) -> int:
    """How many task calls ride the AIMessage that carries this call."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            calls = getattr(m, "tool_calls", None) or []
            if any(tc.get("id") == tool_call_id for tc in calls):
                return sum(1 for tc in calls if tc.get("name") == "task")
    return 1


class PlanBudgetGuard(AgentMiddleware):
    """Reject oversized delegation plans before any worker spawns."""

    def __init__(self, max_subagents: int, max_replans: int = MAX_REPLANS):
        super().__init__()
        self.cap = int(max_subagents)
        self.max_replans = int(max_replans)

    def _verdict(self, request):
        """(reject: bool, batch, done, replans) for this task call."""
        messages = (request.state or {}).get("messages") or []
        done = _executed_delegations(messages)
        batch = _batch_size(messages, request.tool_call.get("id"))
        return (self.cap and done + batch > self.cap), batch, done, \
            _rejected_plans(messages)

    def _rejection(self, request, batch: int, done: int) -> ToolMessage:
        return ToolMessage(
            content=(f"{PLAN_REJECT_PREFIX} This plan delegates {batch} "
                     f"subtasks but only {self.cap - done} more are allowed "
                     f"(budget {self.cap}). No subtask was started. Replan "
                     f"now with at most {self.cap - done} subtasks that "
                     f"together cover the whole objective."),
            tool_call_id=request.tool_call.get("id"), name="task",
            status="error")

    def wrap_tool_call(self, request, handler):
        if request.tool_call.get("name") != "task":
            return handler(request)
        reject, batch, done, replans = self._verdict(request)
        if not reject:
            return handler(request)
        if replans >= self.max_replans:
            from backend.observability.event_adapter import BudgetExceeded
            raise BudgetExceeded("max_subagents", done + batch, self.cap)
        return self._rejection(request, batch, done)

    async def awrap_tool_call(self, request, handler):
        if request.tool_call.get("name") != "task":
            return await handler(request)
        reject, batch, done, replans = self._verdict(request)
        if not reject:
            return await handler(request)
        if replans >= self.max_replans:
            from backend.observability.event_adapter import BudgetExceeded
            raise BudgetExceeded("max_subagents", done + batch, self.cap)
        return self._rejection(request, batch, done)


class ToolBudgetGuard(AgentMiddleware):
    """Per-worker tool budget: past the cap, tools decline instead of run."""

    def __init__(self, max_tool_hops: int):
        super().__init__()
        self.cap = int(max_tool_hops)

    def _spent(self, request) -> int:
        messages = (request.state or {}).get("messages") or []
        return sum(1 for m in messages if isinstance(m, ToolMessage))

    def _exhausted(self, request) -> ToolMessage:
        return ToolMessage(
            content=(f"{TOOL_EXHAUSTED_PREFIX} Tool budget exhausted "
                     f"({self.cap} calls used). Do not call any more tools. "
                     f"Finalize your answer now with what you have."),
            tool_call_id=request.tool_call.get("id"),
            name=request.tool_call.get("name"), status="error")

    def wrap_tool_call(self, request, handler):
        if self.cap and self._spent(request) >= self.cap:
            return self._exhausted(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        if self.cap and self._spent(request) >= self.cap:
            return self._exhausted(request)
        return await handler(request)
