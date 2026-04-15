# Xeon Agent Swarm

An open-source demo showing how a complex query is automatically decomposed into parallel
subtasks, executed against multiple OPEA-served model endpoints (CPU and GPU), and
reassembled into a final answer — with a side-by-side A/B comparison against a single
large-model pipeline.

## What You'll See

| Left panel (Swarm) | Right panel (A/B Baseline) |
|---|---|
| Query decomposed into 3-6 parallel subtasks | Same query sent to one large model |
| Animated task DAG with live status | Tokens stream in character by character |
| Per-task model/hardware badges (CPU / GPU) | Final answer with total latency |
| Synthesized final answer with attribution | |
| Latency comparison bar chart | |

## Architecture

```
User query
    │
    ▼
Orchestrator (Qwen2.5-7B)
    │  decomposes into TaskGraph (3-6 tasks)
    ▼
Fan-out via LangGraph Send API
    ├── Worker[research]  (GPU preferred, Llama-3.1-8B)
    ├── Worker[analysis]  (CPU, Phi-4-mini)
    ├── Worker[code]      (GPU preferred)
    └── Worker[general]   (CPU, Phi-4-mini)
    │
    ▼
Reducer (Qwen2.5-7B)  ←  synthesizes all results
    │
    ▼
WebSocket → Frontend (React + React Flow + Recharts)
```

All model serving is via [OPEA](https://opea-project.github.io/) vLLM images with
OpenVINO/AMX acceleration on Intel Xeon.

## Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (StateGraph + Send fan-out) |
| Model serving | OPEA vLLM (OpenVINO + AMX on Xeon) |
| Structured output | `instructor` (guarantees valid TaskGraph JSON) |
| Backend API | FastAPI + WebSockets |
| Task queue | Redis pub/sub |
| Agent protocols | A2A Agent Cards, MCP (JSON-RPC 2.0 over HTTP) |
| Frontend | React 18 + Vite + Tailwind |
| Graph visualization | React Flow |
| Timing charts | Recharts |
| State management | Zustand |
| Observability | Prometheus counters + histograms |

## Quick Start

### Prerequisites

- Docker and Docker Compose v2
- 16 GB+ RAM for CPU-only stack
- HuggingFace account (free tier) for model downloads

### Steps

```bash
# 1. Clone and configure
git clone https://github.com/your-org/xeon-agent-swarm
cd xeon-agent-swarm
cp .env.example .env
# Edit .env — add your HF_TOKEN at minimum

# 2. Start CPU-only stack
# First run downloads models (~10-20 min depending on connection)
docker compose up

# 3. Start with GPU workers (Intel Gaudi or NVIDIA)
docker compose --profile gpu up

# 4. Open the demo UI
open http://localhost:3000
```

### Sample queries to try

```
Compare the energy efficiency of nuclear, wind, and solar power generation,
including current costs per MWh and carbon footprint per MWh.

Explain the differences between transformer and LSTM architectures for NLP,
with code examples showing how each processes a sequence.

Analyze the economic impacts of remote work on urban real estate markets
since 2020, including effects on commercial and residential sectors.
```

## Repository Layout

```
xeon-agent-swarm/
├── backend/
│   ├── agents/          # orchestrator, worker, reducer, single_model
│   ├── graph/           # LangGraph swarm_graph (fan-out / fan-in)
│   ├── inference/       # Async OpenAI-compatible client wrapper
│   ├── protocols/       # A2A Agent Cards, A2A tasks, MCP server registry
│   ├── queue/           # Redis pub/sub task queue
│   ├── observability/   # Prometheus metrics
│   ├── schemas/         # Pydantic models (TaskSpec, SwarmState, events…)
│   └── main.py          # FastAPI app + WebSocket hub
├── frontend/
│   └── src/
│       ├── components/  # QueryInput, ABPanel, TaskGraph, WorkerCard, …
│       ├── hooks/       # useSwarmSocket (WebSocket + event dispatch)
│       ├── store/       # Zustand swarmStore
│       └── types/       # TypeScript mirrors of backend schemas
├── mcp_servers/
│   ├── web_search/      # Brave/DuckDuckGo MCP server
│   ├── doc_retrieval/   # ChromaDB RAG MCP server
│   └── code_exec/       # Sandboxed Python exec MCP server
├── config/
│   ├── endpoints.yaml   # Model endpoint registry
│   └── worker_roles.yaml # System prompts per worker specialization
└── tests/               # pytest: orchestrator, graph routing, A2A
```

## Configuration

### Model endpoints (`config/endpoints.yaml`)

Edit to point at your OPEA vLLM instances or any OpenAI-compatible API:

```yaml
endpoints:
  - id: orchestrator
    url: "${ORCHESTRATOR_ENDPOINT}"   # set in .env
    model: "${ORCHESTRATOR_MODEL}"
    hardware: cpu
```

### Worker roles (`config/worker_roles.yaml`)

Each role defines a system prompt, preferred hardware, and tools:

```yaml
roles:
  research:
    preferred_hardware: gpu
    tools: [web_search, doc_retrieval]
    system_prompt: |
      You are a research specialist…
```

## Environment Variables

See [.env.example](.env.example) for the full list. Key variables:

| Variable | Description |
|---|---|
| `HF_TOKEN` | HuggingFace token for gated model downloads |
| `ORCHESTRATOR_ENDPOINT` | vLLM URL for the orchestrator model |
| `WORKER_CPU_ENDPOINT` | vLLM URL for CPU worker model |
| `WORKER_GPU_ENDPOINT` | vLLM URL for GPU worker model (optional) |
| `SINGLE_MODEL_ENDPOINT` | vLLM URL for A/B baseline model |
| `REDIS_URL` | Redis connection string |
| `BRAVE_API_KEY` | Optional — MCP web_search uses DuckDuckGo if not set |

## Observability

- **Prometheus** metrics at `http://localhost:9090` (scrapes `/metrics` on the backend)
- Metrics include per-task latency histograms, run counters, and WebSocket connection gauges
- A2A task state is persisted in Redis (visible with `redis-cli monitor`)

## Running Tests

```bash
# Install test dependencies
pip install -r backend/requirements.txt pytest pytest-asyncio

# Run all tests
pytest tests/ -v
```

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Start a swarm run; returns `{run_id}` |
| `GET` | `/run/{run_id}` | Fetch final `RunResult` |
| `WS` | `/ws/{run_id}` | Stream `SwarmEvent` objects in real time |
| `GET` | `/agents` | List all A2A Agent Cards |
| `GET` | `/.well-known/agent.json` | A2A discovery for this host |
| `GET` | `/health` | Liveness check |
| `GET` | `/metrics` | Prometheus metrics |

## Security Notes

This is a demo. Before any production use:

- Add authentication to FastAPI endpoints (OAuth2 / API keys)
- Scope MCP server tool permissions (code_exec runs in a restricted sandbox but has no network isolation)
- Validate and sanitize all LLM outputs before rendering
- Review A2A Agent Card exposure (currently no auth)
- Replace `allow_origins=["*"]` CORS with explicit origins
- Use secrets management (Vault, AWS Secrets Manager) instead of `.env` files

## License

Apache 2.0
