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

mcp_calls_total = Counter(
    "swarm_mcp_calls_total",
    "Total MCP tool calls",
    ["tool"],
)

websocket_connections = Gauge(
    "swarm_websocket_connections",
    "Current number of active WebSocket connections",
)

# ── Tier routing + cost (tier-router migration §5) ───────────────────────────

# How often each tier was actually served vs requested — the routing distribution
# that makes the "auto picked T2, not T5" story visible in metrics.
tier_calls_total = Counter(
    "orchestrator_tier_calls_total",
    "LLM calls by requested and observed router tier",
    ["tier_requested", "tier_observed", "cache_hit"],
)

# Per-run cost rollup (illustrative pricing — see observability/cost.py).
run_cost_usd = Histogram(
    "orchestrator_run_cost_usd",
    "Per-run cost at served tiers (USD, illustrative)",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

run_savings_pct = Histogram(
    "orchestrator_run_savings_pct",
    "Per-run decomposed-vs-monolithic-T5 savings (%)",
    buckets=[0, 10, 25, 50, 70, 85, 95, 100],
)
