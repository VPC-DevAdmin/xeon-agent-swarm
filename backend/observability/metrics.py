"""
Prometheus counters and histograms per agent.
Exposed at GET /metrics via the prometheus_fastapi_instrumentator or manually.
"""
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

# Use the default registry so prometheus_client exports them automatically.

tasks_total = Counter(
    "swarm_tasks_total",
    "Total number of tasks executed",
    ["status", "type", "hardware"],
)

task_latency_seconds = Histogram(
    "swarm_task_latency_seconds",
    "Task execution latency in seconds",
    ["type", "hardware"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

runs_total = Counter(
    "swarm_runs_total",
    "Total number of swarm runs started",
)

run_latency_seconds = Histogram(
    "swarm_run_latency_seconds",
    "End-to-end run latency in seconds",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

active_runs = Gauge(
    "swarm_active_runs",
    "Number of runs currently in progress",
)

single_model_latency_seconds = Histogram(
    "swarm_single_model_latency_seconds",
    "Single-model A/B baseline latency in seconds",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

mcp_calls_total = Counter(
    "swarm_mcp_calls_total",
    "Total MCP tool calls",
    ["tool"],
)

websocket_connections = Gauge(
    "swarm_websocket_connections",
    "Current number of active WebSocket connections",
)
