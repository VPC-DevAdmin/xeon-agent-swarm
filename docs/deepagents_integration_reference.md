# deepagents Integration Reference (verified against 0.6.10)

Hand this to Claude Code alongside the execution plan. It pins versions and records the confirmed API shapes so nothing about deepagents is guessed. Verified from the LangChain docs, the deepagents reference, and the GitHub source as of June 2026.

---

## Versions and install

deepagents current release is **0.6.10** (requires Python >=3.11). It is built on LangGraph and pulls `langchain`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai`, `langsmith`, and `wcmatch` as dependencies. `create_deep_agent` returns a compiled LangGraph graph, so LangGraph streaming, persistence, and checkpointing all apply.

Pin deepagents exactly and let it constrain the LangChain/LangGraph stack, then freeze. Do not hand-pin transitive LangChain versions from memory; install deepagents first and `pip freeze` to capture the matching set.

```
# requirements.in (top-level intent; freeze after install)
deepagents==0.6.10
langchain-openai            # ChatOpenAI pointed at the gateway (model.py)
langgraph-checkpoint-sqlite # AsyncSqliteSaver
langchain-mcp-adapters      # turn the MCP toolbox servers into LangChain tools
# app stack (already in the old repo)
fastapi
uvicorn
aiosqlite
SQLAlchemy
APScheduler
croniter
# optional
deepagents[quickjs]         # only if you want the sandboxed eval tool
```

Build step for Claude Code: `pip install -r requirements.in` then `pip freeze > requirements.txt`. The plan's instruction to "verify schemas against the installed reference" means running `python -c "import deepagents, inspect; print(inspect.signature(deepagents.create_deep_agent))"` after install, not guessing.

---

## create_deep_agent signature (the params ADL uses)

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model=...,             # a LangChain chat model (ADL passes ChatOpenAI -> gateway)
    tools=[...],           # main-agent tools (MCP tools via langchain-mcp-adapters)
    system_prompt="...",   # orchestrator/planner instructions
    subagents=[...],       # declarative SubAgent specs (workers), see below
    interrupt_on={...},    # HITL gates; REQUIRES a checkpointer
    checkpointer=...,      # AsyncSqliteSaver; required for HITL and resume
    # other passthrough-to-create_agent params: store, state_schema, middleware,
    # permissions, backend, context_schema, response_format, name, cache, debug
)
```

Many parameters are passed straight through to LangChain's `create_agent`. The model can be any LangChain chat model that supports tool calling, which is why pointing `ChatOpenAI` at the OpenAI-compatible gateway works.

---

## Subagent spec (the worker profiles)

Workers are declarative `SubAgent` dicts, dispatched through the built-in `task` tool. Required keys: **name, description, system_prompt**. Optional overrides: **tools, model, middleware, interrupt_on, skills**.

```python
{
    "name": "researcher",
    "description": "Gather and summarize facts for one subtask.",
    "system_prompt": "You are a focused researcher...",
    "tools": [...],          # the MCP tools granted to this role
    "model": mf.auto(),      # workers run on auto so the router classifies them
}
```

Notes that matter for ADL:
- If no subagent named `general-purpose` is provided, deepagents auto-adds a default one (unless a harness profile disables it). Keep it as the fallback when the planner's subtask fits no named role.
- Each subagent has an isolated context window; the parent only sees the subagent's final message, not its intermediate work. This is the context isolation the design relies on.
- Subagents can set `response_format` (a Pydantic schema) to return structured JSON instead of prose. Useful if a worker must hand back a typed result.
- Port `worker_roles.yaml` straight into this list. deepagents also supports loading subagents from YAML frontmatter under `.deepagents/agents/`, which may map cleanly onto your existing config.

---

## interrupt_on and HITL (with two live caveats)

`interrupt_on` maps a tool name to its gate: `True` (default decisions approve/edit/reject/respond), `False` (no interrupt), or `{"allowed_decisions": [...]}` to restrict. A checkpointer is **required** whenever any interrupt is configured. Resume with `langgraph.types.Command(resume=...)` against the same `thread_id`.

Two open bugs to design around, both confirmed in the deepagents issue tracker:
- **Subagent edit/reject interrupts are broken (issue #554).** Only `approve` works reliably for subagent-level interrupts. Keep your HITL approval at the **main agent** level (the plan-approval gate), where it works. Do not depend on per-subagent edit/reject.
- **Subagents lack their own checkpoint persistence (issue #573).** Only the main agent gets the checkpointer; subagent tool history can be truncated in state queries. This reinforces the design: the **app DB is the system of record**, and the event adapter must capture subagent activity from the stream as it happens rather than reconstructing it from checkpointer state afterward.

---

## Checkpointer (AsyncSqliteSaver)

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

async with AsyncSqliteSaver.from_conn_string(os.environ["CHECKPOINT_DB"]) as checkpointer:
    agent = build_agent(checkpointer, mcp_tools)
    ...
```

`from_conn_string` is an async context manager that sets up the SQLite tables on first use. In FastAPI, manage its lifecycle in the lifespan handler so one saver instance lives for the app. It self-manages its schema; do not model its tables in your SQLAlchemy layer. Verify the exact import path against the installed `langgraph-checkpoint-sqlite`.

State note: if you extend graph state, subclass `DeepAgentState` (`from deepagents.graph import DeepAgentState`) so the built-in DeltaChannel reducer on `messages` is preserved. Plain TypedDicts will break message accumulation.

---

## Streaming (feeds the event adapter)

deepagents 0.6 exposes **typed streaming projections** for messages, tool calls, subagents, and custom application events, so you subscribe to exactly the channel you need instead of parsing raw output. The subagents projection is what drives the live worker cards and the task graph in the UI. Use `agent.astream(..., config)` and route the projected events into `event_adapter.handle(...)`. Set `thread_id == run_id` and tag each invocation (`tier_req:<tier>`, owning `step_id`) so the tier/cost callback rows attribute to the right step.

---

## MCP toolbox wiring

The library consumes MCP tools as LangChain tools. Use `langchain-mcp-adapters` to connect your existing `mcp_servers/` (web_search, code_exec, doc_retrieval) and convert their tools, then pass the granted subset to the main agent and per-role subsets to each subagent profile. Per-role grants come from `worker_roles.yaml`, not SQL. (deepagents-cli has its own `mcp-servers` registry, but that is for the CLI/Platform path, not the library; you do not need it here.)

---

## Things you do NOT need (avoid scope creep)

- **Harness profiles** (`register_harness_profile`) tune behavior per model provider. Since the gateway hides provider identity behind tier ids, ADL has nothing to key a profile on. Skip them.
- **Async subagents** target remote LangGraph Platform servers. ADL is single-node and synchronous. Skip them.
- **The virtual filesystem context-offload and skills** are for long autonomous runs. This bounded decompose-fan-out-reduce pipeline does not need them; the default `StateBackend` is fine.
- **deepagents-cli / LangGraph Platform deployment.** Not used; ADL deploys standalone on the R470.

---

## Design reconciliation for the plan

One correction to fold back into the execution plan and env: in deepagents the **planner and the final synthesis are the same main agent**, so pinning the main agent to `ADL_PLANNER_TIER` also pins synthesis to that tier. `ADL_SYNTHESIS_TIER` only takes effect if you split synthesis into a dedicated subagent on its own model. Decide one of:
- Simplest: let synthesis run at the planner tier (main agent composes the answer). Drop `ADL_SYNTHESIS_TIER`.
- Cheaper synthesis: add a `synthesizer` subagent with `model=mf.for_tier(ADL_SYNTHESIS_TIER)` (or `mf.auto()`), and have the orchestrator prompt delegate the write-up instead of composing it.
