"""
A2A Agent Card definitions and discovery.

Each agent advertises its capabilities at /.well-known/agent.json.
The orchestrator uses these cards to discover available workers at startup
rather than having them hardcoded.
"""
import os
import yaml
from pathlib import Path


ORCHESTRATOR_CARD = {
    "id": "orchestrator-agent",
    "name": "Orchestrator",
    "description": "Decomposes complex queries into parallel subtasks",
    "version": "0.1.0",
    "endpoint": "/agents/orchestrator",
    "capabilities": ["decompose", "route"],
    "input_modes": ["text"],
    "output_modes": ["task_graph"],
    "authentication": {"type": "none"},
}

REDUCER_CARD = {
    "id": "reducer-agent",
    "name": "Reducer",
    "description": "Synthesizes parallel subtask results into a final answer",
    "version": "0.1.0",
    "endpoint": "/agents/reducer",
    "capabilities": ["synthesize"],
    "input_modes": ["agent_results"],
    "output_modes": ["text"],
    "authentication": {"type": "none"},
}

SINGLE_MODEL_CARD = {
    "id": "single-model-agent",
    "name": "Single Model",
    "description": "A/B baseline: single large model, no decomposition",
    "version": "0.1.0",
    "endpoint": "/agents/single",
    "capabilities": ["general"],
    "input_modes": ["text"],
    "output_modes": ["text"],
    "authentication": {"type": "none"},
}


def _load_roles() -> dict:
    cfg_path = Path(os.getenv("CONFIG_DIR", "/app/config")) / "worker_roles.yaml"
    if not cfg_path.exists():
        cfg_path = Path(__file__).parent.parent.parent / "config" / "worker_roles.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)["roles"]


def build_worker_cards() -> list[dict]:
    """Build one Agent Card per (role, hardware) combination."""
    cards = []
    try:
        roles = _load_roles()
    except Exception:
        return cards

    for role_name, role_cfg in roles.items():
        for hw in ["cpu", "gpu"]:
            cards.append({
                "id": f"worker-{role_name}-{hw}",
                "name": f"Worker ({role_name})",
                "description": role_cfg.get("description", ""),
                "version": "0.1.0",
                "endpoint": "/agents/worker",
                "capabilities": [role_name],
                "input_modes": ["task_spec"],
                "output_modes": ["agent_result"],
                "hardware": hw,
                "tools": role_cfg.get("tools", []),
                "authentication": {"type": "none"},
            })
    return cards


def all_agent_cards() -> list[dict]:
    return [ORCHESTRATOR_CARD, REDUCER_CARD, SINGLE_MODEL_CARD] + build_worker_cards()
