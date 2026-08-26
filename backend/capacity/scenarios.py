"""Load the fixed capacity-test scenarios (config/capacity_scenarios.yaml)."""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

_CONFIG_DIR = os.getenv("CONFIG_DIR", "config")
_PATH = os.path.join(_CONFIG_DIR, "capacity_scenarios.yaml")

def benchmark_version() -> int:
    return int(_load_file().get("version", 1))


def load_e2e_workflows() -> dict[str, dict]:
    """Real workflows for the end-to-end runtime mode.

    {id: {name, query, think_ms, budgets}} — budgets are part of the workload
    definition: they fix the size of the work unit, and a run that hits them
    counts as a failed workflow (the unit did not complete)."""
    out = {}
    for wid, w in (_load_file().get("e2e_workflows") or {}).items():
        out[wid] = {"name": w.get("name", wid), "query": w.get("query", ""),
                    "think_ms": int(w.get("think_ms", 3000)),
                    "budgets": dict(w.get("budgets") or {}) or None,
                    # Self-contained units strip catalog/builtin tool grants —
                    # otherwise static role grants (research -> web_search)
                    # invite tool loops the prompt forbids. `tools:` lists the
                    # benchmark's own deterministic tools (bench_record: one
                    # real dispatch + durable write per worker).
                    "enabled_tools": list(w.get("tools") or []),
                    "toolless": bool(w.get("toolless", False))}
    return out


def e2e_tile_sessions() -> list[str]:
    wfs = load_e2e_workflows()
    raw = _load_file().get("e2e_tile") or {}
    tile = {wid: int(n) for wid, n in raw.items() if wid in wfs and int(n) > 0}         or {wid: 1 for wid in wfs}
    out: list[str] = []
    for wid, n in tile.items():
        out.extend([wid] * n)
    return out


def load_e2e_tile() -> dict[str, int]:
    wfs = load_e2e_workflows()
    raw = _load_file().get("e2e_tile") or {}
    return {wid: int(n) for wid, n in raw.items() if wid in wfs and int(n) > 0}         or {wid: 1 for wid in wfs}


# ── seeded synthetic corpus ────────────────────────────────────────────────────
# Prompts are built from seeded, per-call-varied word sequences instead of one
# repeated filler sentence. Repeated identical text let RadixAttention-style
# prefix caches serve most of the prefill for free, exaggerating capacity; a
# varied corpus exercises realistic attention while staying EXACTLY reproducible
# for a given (seed, session, call) key. Sizing remains the documented ~4
# chars/token approximation (tokenizer-exact calibration is a planned upgrade).
_VOCAB = (
    "the quarterly report shows revenue increased across enterprise segments while "
    "operating margins compressed due to infrastructure investment and headcount growth "
    "customers migrating workloads toward managed inference platforms cite latency "
    "predictability compliance requirements and total cost of ownership as primary "
    "decision factors meanwhile engineering teams evaluate quantization strategies "
    "batching schedulers cache hierarchies memory bandwidth utilization and thermal "
    "envelopes when planning capacity for concurrent agent sessions each running "
    "retrieval augmented pipelines with tool integrations database queries document "
    "processing validation stages scheduled digests and multi turn conversations that "
    "accumulate context over time the analysis compares deployment options including "
    "dedicated servers cloud endpoints and hybrid routing policies weighing throughput "
    "against per token economics service levels operational complexity vendor risk and "
    "upgrade paths findings indicate sustained utilization patterns differ sharply from "
    "burst traffic profiles requiring distinct provisioning headroom monitoring alerts "
    "and failover procedures summarized recommendations follow with supporting metrics"
).split()


def synthetic_text(vary_key: str, n_chars: int) -> str:
    """Deterministic varied text: same key -> same text; different keys share no
    meaningful prefix. Pure function of (vary_key, n_chars)."""
    import random as _random
    rng = _random.Random(vary_key)
    words: list[str] = []
    size = 0
    while size < n_chars:
        w = _VOCAB[rng.randrange(len(_VOCAB))]
        words.append(w)
        size += len(w) + 1
    return " ".join(words)[:n_chars]


_file_cache: tuple[float, dict] | None = None


def _load_file() -> dict:
    """mtime-cached: config parses once per process (standard practice); an
    edited file is picked up on the next call."""
    global _file_cache
    mtime = os.path.getmtime(_PATH)
    if _file_cache is None or _file_cache[0] != mtime:
        with open(_PATH) as f:
            _file_cache = (mtime, yaml.safe_load(f) or {})
    return _file_cache[1]


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


def build_prompt(step: dict, scenario_name: str, extra_context_tokens: int = 0,
                 *, vary_key: str = "0", cache_mode: str = "warm") -> list[dict]:
    """Deterministic chat messages approximating prompt_tokens + carried context.

    extra_context_tokens models the agent's accumulated state — prior step
    outputs, injected tool results, session history — so prompts grow the way a
    real agent's do instead of staying chatbot-flat.

    vary_key seeds the corpus so every call's body is different (no prefix-cache
    freebies) yet exactly reproducible for a given benchmark seed. cache_mode
    "warm" keeps the short shared system preamble (agents in production share
    their system prompt — the realistic case); "cold" salts it per call so
    NOTHING is prefix-cacheable."""
    target_chars = (int(step.get("prompt_tokens", 200)) + int(extra_context_tokens)) * 4
    body = synthetic_text(vary_key, target_chars)
    system = (f"You are the '{step.get('label', 'step')}' stage of a fixed "
              f"'{scenario_name}' benchmark scenario. Answer the task directly.")
    if cache_mode == "cold":
        system = f"[run {vary_key}] " + system
    return [
        {"role": "system", "content": system},
        {"role": "user",
         "content": f"Benchmark task ({step.get('label')}): process the following "
                    f"context and produce your stage's output.\n\n{body}"},
    ]
