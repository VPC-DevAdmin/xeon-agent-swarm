# Xeon Agent Swarm — Implementation Spec

See the full specification in the project's initial design document.
The implemented repository follows this spec exactly.

Key design decisions preserved from the spec:

| Decision | Rationale |
|---|---|
| Single `docker compose up` | Lowest friction for demo |
| GPU workers behind `profiles: ["gpu"]` | CPU-only stack works out of the box |
| OPEA vLLM images for all inference | Leverages AMX/OpenVINO optimization on Xeon |
| LangGraph for orchestration | Code mirrors architecture; readers can follow it |
| `instructor` for structured output | Guarantees valid TaskGraph from small models |
| WebSocket event streaming | UI shows live progress, not just a final answer |
| Zustand for frontend state | Minimal boilerplate; all events flow through one store |
| React Flow for task graph | Handles DAG layout automatically; battle-tested |
| MCP over HTTP (not stdio) | Servers run as separate Docker containers |
| A2A Agent Cards at `/.well-known/agent.json` | Standard discovery; workers are pluggable |
| Redis for A2A task state | Resumable; visible in any Redis client |

Refer to README.md for quick-start instructions.
