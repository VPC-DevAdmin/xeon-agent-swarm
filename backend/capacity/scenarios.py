"""Load the fixed capacity-test scenarios (config/capacity_scenarios.yaml)."""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

_CONFIG_DIR = os.getenv("CONFIG_DIR", "config")
_PATH = os.path.join(_CONFIG_DIR, "capacity_scenarios.yaml")

# Deterministic filler used to pad prompts to a target token count (~4 chars/token).
_FILLER = (
    "Throughput benchmarking sentence about agents, routers, tiers, tokens, "
    "latency, memory bandwidth, and Xeon cores. "
)


@lru_cache(maxsize=1)
def load_scenarios() -> dict[str, dict]:
    with open(_PATH) as f:
        data = yaml.safe_load(f) or {}
    out = data.get("scenarios", {})
    for sid, s in out.items():
        s.setdefault("name", sid)
        s.setdefault("blurb", "")
        s.setdefault("complexity", "medium")
        s.setdefault("think_ms", 1000)
        s.setdefault("steps", [])
    return out


def scenario_list() -> list[dict]:
    """Catalog shape for the API/UI: id folded in, calls/tokens summarized."""
    items = []
    for sid, s in load_scenarios().items():
        items.append({
            "id": sid,
            "name": s["name"],
            "blurb": s["blurb"],
            "complexity": s["complexity"],
            "calls_per_loop": len(s["steps"]),
            "tokens_out_per_loop": sum(st.get("max_tokens", 0) for st in s["steps"]),
        })
    return items


def build_prompt(step: dict, scenario_name: str) -> list[dict]:
    """Deterministic chat messages approximating the step's prompt_tokens."""
    target_chars = int(step.get("prompt_tokens", 200)) * 4
    body = (_FILLER * (target_chars // len(_FILLER) + 1))[:target_chars]
    return [
        {"role": "system",
         "content": f"You are the '{step.get('label', 'step')}' stage of a fixed "
                    f"'{scenario_name}' benchmark scenario. Answer the task directly."},
        {"role": "user",
         "content": f"Benchmark task ({step.get('label')}): process the following "
                    f"context and produce your stage's output.\n\n{body}"},
    ]
