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
def _load_file() -> dict:
    with open(_PATH) as f:
        return yaml.safe_load(f) or {}


def load_tile() -> dict[str, int]:
    """The reference tile (ACU): {scenario_id: sessions}, filtered to scenarios
    that exist. Falls back to one of each when the file defines no tile."""
    scen = load_scenarios()
    raw = _load_file().get("tile") or {}
    tile = {sid: int(n) for sid, n in raw.items() if sid in scen and int(n) > 0}
    return tile or {sid: 1 for sid in scen}


def tile_sessions() -> list[str]:
    """The tile expanded to an ordered session assignment list."""
    out: list[str] = []
    for sid, n in load_tile().items():
        out.extend([sid] * n)
    return out


@lru_cache(maxsize=1)
def load_scenarios() -> dict[str, dict]:
    data = _load_file()
    out = data.get("scenarios", {})
    for sid, s in out.items():
        s.setdefault("name", sid)
        s.setdefault("blurb", "")
        s.setdefault("complexity", "medium")
        s.setdefault("think_ms", 1000)
        s.setdefault("session_turns", 1)   # loops before the session context resets
        s.setdefault("context_cap", 6000)  # ceiling on carried context tokens
        s.setdefault("steps", [])
        for st in s["steps"]:
            st.setdefault("carry_context", False)
            st.setdefault("tool_calls", 0)
            st.setdefault("tool_result_tokens", 400)
            st.setdefault("tool_latency_ms", 300)
    return out


def scenario_list() -> list[dict]:
    """Catalog shape for the API/UI: id folded in, calls/tokens summarized."""
    items = []
    for sid, s in load_scenarios().items():
        tool_calls = sum(st["tool_calls"] for st in s["steps"])
        items.append({
            "id": sid,
            "name": s["name"],
            "blurb": s["blurb"],
            "complexity": s["complexity"],
            # every tool round-trip is an additional LLM call (the continuation)
            "calls_per_loop": len(s["steps"]) + tool_calls,
            "tool_calls_per_loop": tool_calls,
            "tokens_out_per_loop": sum(st.get("max_tokens", 0) * (1 + st["tool_calls"])
                                       for st in s["steps"]),
            "tokens_in_per_loop": sum(st.get("prompt_tokens", 0) for st in s["steps"]),
            "think_ms": s["think_ms"],
            "session_turns": s["session_turns"],
            "context_cap": s["context_cap"],
            # The full loop, so the UI can show exactly what this agent does.
            "steps": [{"label": st.get("label", f"step {i+1}"),
                       "prompt_tokens": st.get("prompt_tokens", 0),
                       "max_tokens": st.get("max_tokens", 0),
                       "carry_context": st["carry_context"],
                       "tool_calls": st["tool_calls"],
                       "tool_result_tokens": st["tool_result_tokens"]}
                      for i, st in enumerate(s["steps"])],
        })
    return items


def build_prompt(step: dict, scenario_name: str, extra_context_tokens: int = 0) -> list[dict]:
    """Deterministic chat messages approximating prompt_tokens + carried context.

    extra_context_tokens models the agent's accumulated state — prior step
    outputs, injected tool results, session history — so prompts grow the way a
    real agent's do instead of staying chatbot-flat."""
    target_chars = (int(step.get("prompt_tokens", 200)) + int(extra_context_tokens)) * 4
    body = (_FILLER * (target_chars // len(_FILLER) + 1))[:target_chars]
    return [
        {"role": "system",
         "content": f"You are the '{step.get('label', 'step')}' stage of a fixed "
                    f"'{scenario_name}' benchmark scenario. Answer the task directly."},
        {"role": "user",
         "content": f"Benchmark task ({step.get('label')}): process the following "
                    f"context and produce your stage's output.\n\n{body}"},
    ]
