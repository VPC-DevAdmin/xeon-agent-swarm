"""
backend/agents/core.py

deepagents assembly. Verified against deepagents 0.6.10 (see
deepagents_integration_reference.md).

Shape:
  - The MAIN agent is the planner/orchestrator. Its model is pinned (ADL_PLANNER_TIER,
    default T5) because plan quality cannot tolerate a downgrade. In deepagents the
    planner AND the final synthesis are the SAME main agent, so this tier also governs
    synthesis (per the reference's reconciliation note). For cheaper synthesis, add a
    dedicated `synthesizer` subagent on mf.auto()/for_tier(ADL_SYNTHESIS_TIER) and have
    the orchestrator prompt delegate the write-up instead of composing it.
  - WORKERS are declarative subagents (profiles.py, from worker_roles.yaml), invoked
    through the built-in `task` tool. Each runs on mf.auto() so the router classifies it.
  - The checkpointer (AsyncSqliteSaver) is REQUIRED for human-in-the-loop and gives
    resume-after-failure. It owns live state; the app DB stays the system of record.

Design caveats from the reference (deepagents 0.6.10):
  - Delegation is LLM-driven and sequential by default; this is not an explicit parallel
    DAG executor. Fine for the demo. Do not force a parallel DAG onto the harness.
  - Subagent edit/reject interrupts are buggy (#554) and subagents lack their own
    checkpoint (#573). Keep HITL approval at the MAIN-agent level (plan approval), and
    let the event adapter capture subagent activity from the stream as it happens.
"""
from __future__ import annotations

import os

from deepagents import create_deep_agent

# AsyncSqliteSaver lives in langgraph-checkpoint-sqlite. Verify the import path
# against the installed version (deepagents_integration_reference.md).
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.inference.model import ModelFactory
from backend.agents.profiles import build_subagent_profiles
from backend.agents.toolbox import build_toolbox


ORCHESTRATOR_PROMPT = """You decompose the user's objective into the smallest useful set
of subtasks and delegate each to a worker subagent via the task tool. Prefer 2 to 5
subtasks. Delegate independent subtasks before dependent ones; pass an upstream
subtask's result to a dependent one. After the workers return, compose a single
coherent answer that uses their specific findings — numbers, citations, comparisons —
not vague generalities. Do not author new agent types; use the worker types available
to you.

Do NOT ask the user clarifying questions. The objective is the only input you get;
proceed with reasonable, explicit assumptions and decompose immediately. State any
assumptions you made in the final answer. Always delegate via the task tool — do not
answer the objective yourself without delegating.

Your final message is the deliverable the user sees. Output ONLY the answer itself —
no preamble, no status narration (never write things like "Composing the answer now"
or "I have enough material"), and no meta-commentary about your process."""

# Appended to the orchestrator prompt only when plan approval is enabled. The one-shot
# submit_plan tool is the approval gate: called exactly once, before any delegation, so
# the run pauses precisely once (unlike write_todos, which fires on every todo edit and
# is inherited by every subagent).
PLAN_APPROVAL_SUFFIX = """

PLAN APPROVAL: Before delegating anything, call the submit_plan tool EXACTLY ONCE with
your finalized subtask plan (a short numbered list) and wait for it to return. Only after
it returns approval may you delegate via the task tool. Never call submit_plan more than
once, and never delegate before it has returned."""

# Gate sensitive tools at the MAIN agent (checkpointer is required and set below).
# Values: True (approve/edit/reject/respond) or {"allowed_decisions": [...]}.
# Subagent-level edit/reject is buggy in 0.6.10 (#554) — keep gates at the main
# agent, where approve works reliably (deepagents_integration_reference.md).
INTERRUPTS: dict = {
    # "ticket_create": {"allowed_decisions": ["approve", "reject"]},
    # "code_exec": True,
}

# The tool whose call is the plan-approval gate. Default is our dedicated one-shot
# `submit_plan` tool (granted to the main agent only, in build_agent), which the
# orchestrator calls exactly once before delegating — so the run pauses precisely once.
# (deepagents' built-in `write_todos` is unsuitable: it fires on every todo edit and is
# inherited by every subagent, causing repeated + concurrent interrupts.) Override via
# ADL_PLAN_TOOL only to gate a different tool.
_PLAN_TOOL = os.environ.get("ADL_PLAN_TOOL", "submit_plan")


def _plan_approval_on() -> bool:
    return os.environ.get("ADL_PLAN_APPROVAL", "").strip().lower() in ("1", "true", "yes")


def build_submit_plan_tool():
    """The one-shot plan-approval gate tool (main agent only). Executing it (post-approve)
    just returns a go-ahead; its whole purpose is to be the single interrupt point."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class SubmitPlanArgs(BaseModel):
        plan: str = Field(description="Your finalized subtask plan as a short numbered list.")

    def _submit_plan(plan: str) -> str:
        return "Plan approved. Proceed to delegate the subtasks via the task tool."

    return StructuredTool.from_function(
        func=_submit_plan,
        name="submit_plan",
        description=("Submit your finalized subtask plan for one-time human approval BEFORE any "
                     "delegation. Call EXACTLY ONCE, right after deciding the plan and before the "
                     "task tool. Returns approval to proceed."),
        args_schema=SubmitPlanArgs,
    )


def build_interrupts() -> dict:
    """Assemble interrupt_on from env (plan §4.5). Plan-approval is the main-agent
    gate that works around #554; sensitive-tool gates are opt-in.

      ADL_PLAN_APPROVAL=1            → pause after planning for approve/reject.
      ADL_SENSITIVE_TOOLS=a,b,c      → gate each named tool (approve/reject).
    """
    interrupts = dict(INTERRUPTS)
    if _plan_approval_on():
        interrupts[_PLAN_TOOL] = {"allowed_decisions": ["approve", "reject"]}
    for tool in (os.environ.get("ADL_SENSITIVE_TOOLS", "") or "").split(","):
        tool = tool.strip()
        if tool:
            interrupts[tool] = {"allowed_decisions": ["approve", "reject"]}
    return interrupts


def build_agent(checkpointer: AsyncSqliteSaver, mcp_tools: list | None = None,
                tools_by_name: dict | None = None):
    """Assemble the deep agent.

    mcp_tools:    LangChain tools granted to the MAIN agent (the planner). Usually a
                  small read-only set; most tool use happens inside subagents.
    tools_by_name: {nickname: LangChain tool} so each profile gets its per-role grant
                  (profiles.py resolves the worker_roles.yaml grants). Defaults to the
                  full managed toolbox (toolbox.build_toolbox) when not supplied.
    """
    mf = ModelFactory()
    planner_tier = os.environ.get("ADL_PLANNER_TIER", "T5")
    if tools_by_name is None:
        tools_by_name = build_toolbox()

    # Plan approval (opt-in): grant the one-shot submit_plan gate to the MAIN agent only
    # (subagents never receive it, so they cannot inherit or re-trigger the interrupt) and
    # tell the orchestrator to call it once before delegating. Gate applies to _PLAN_TOOL.
    main_tools = list(mcp_tools or [])
    system_prompt = ORCHESTRATOR_PROMPT
    if _plan_approval_on() and _PLAN_TOOL == "submit_plan":
        main_tools.append(build_submit_plan_tool())
        system_prompt = ORCHESTRATOR_PROMPT + PLAN_APPROVAL_SUFFIX

    return create_deep_agent(
        model=mf.for_tier(planner_tier),                      # main agent: planner+synthesis
        tools=main_tools,
        system_prompt=system_prompt,
        subagents=build_subagent_profiles(mf, tools_by_name),  # workers on auto
        interrupt_on=build_interrupts(),                        # HITL at main-agent level
        checkpointer=checkpointer,                              # REQUIRED for HITL + resume
    )


# --- Run lifecycle (reference sketch — wired by main.py + event_adapter in P2/P3) ---
# thread_id MUST equal run_id so the checkpointer and the event adapter line up.
#
#   config = {"configurable": {"thread_id": run_id},
#             "tags": [f"tier_req:{planner_tier}"]}     # for RouteCaptureHandler
#   async with AsyncSqliteSaver.from_conn_string(os.environ["CHECKPOINT_DB"]) as cp:
#       agent = build_agent(cp, mcp_tools, tools_by_name)
#       async for event in agent.astream({"messages": objective}, config):
#           event_adapter.handle(event)                 # -> CloudEvents + Step/Attempt rows
#
# Tag each worker subtask with tier_req:auto and the owning step_id so callback rows
# attribute correctly. Subscribe to the subagents projection for spawn/return events.
