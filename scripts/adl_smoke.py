#!/usr/bin/env python
"""
ADL Stage-1 acceptance harness (P0 + P1).

Run with the ADL venv that has deepagents 0.6.10 installed:

    /home/devadmin/.venv-adl/bin/python scripts/adl_smoke.py

Required env (the gateway runs in proxy auth mode, so a secret is mandatory):

    ROUTER_BASE=http://localhost:8900       # default if unset
    SR_AUTH_MODE=proxy                       # the live gateway's mode
    SR_AUTH_EMAIL=<email>                     # proxy identity
    SR_PROXY_SECRET=<secret>                  # shared front-proxy secret
    CONFIG_DIR=config                         # worker_roles.yaml lives here
    ADL_PLANNER_TIER=T5

P0 — ModelFactory round-trips `auto` and `for_tier('T5')` through the gateway and
     surfaces the routed tier from the x-vsr-* headers.
P1 — build_agent decomposes a canned objective into a 2..5 subtask plan bound to
     profiles, with zero hand-authored agents.

Exit code 0 only if every gate passes.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Make `backend` importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Convenience: load gateway credentials from .env.adl at the repo root if present,
# so they don't have to be exported inline. Real .env stays the old-engine config.
def _load_env_adl() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.adl")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_adl()
os.environ.setdefault("ROUTER_BASE", "http://localhost:8900")
os.environ.setdefault("CONFIG_DIR", "config")
os.environ.setdefault("ADL_PLANNER_TIER", "T5")


def _hr(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def _observed_tier(msg) -> str | None:
    """Pull the routed tier from response headers (or body fallback on cache hit)."""
    meta = getattr(msg, "response_metadata", {}) or {}
    headers = {k.lower(): v for k, v in (meta.get("headers", {}) or {}).items()}
    return headers.get("x-vsr-selected-model") or meta.get("model_name") or meta.get("model")


def p0_model_factory() -> bool:
    from backend.inference.model import ModelFactory

    mf = ModelFactory()
    ok = True

    for label, model in (("auto", mf.auto()), ("for_tier(T5)", mf.for_tier("T5"))):
        try:
            resp = model.invoke("Reply with the single word: pong")
            tier = _observed_tier(resp)
            text = (resp.content or "").strip()[:60]
            print(f"  [{label:14}] tier_observed={tier!r}  reply={text!r}")
            if not text:
                print(f"  [{label:14}] FAIL: empty reply")
                ok = False
        except Exception as e:  # noqa: BLE001 — harness surfaces any failure
            print(f"  [{label:14}] FAIL: {type(e).__name__}: {str(e)[:160]}")
            ok = False
    return ok


async def p1_decomposition() -> bool:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command  # noqa: F401 — import-path verification

    from backend.agents.core import build_agent
    from backend.agents.profiles import build_subagent_profiles
    from backend.inference.model import ModelFactory

    mf = ModelFactory()
    profiles = build_subagent_profiles(mf, tools_by_name=None)
    profile_names = {p["name"] for p in profiles}
    print(f"  profiles loaded: {sorted(profile_names)}")

    # Self-contained objective: concrete subjects so the planner decomposes instead of
    # asking for clarification. (The orchestrator prompt also forbids clarifying questions.)
    objective = (
        "Write a brief comparing vLLM and llama.cpp for CPU-only LLM inference. "
        "Research each project's throughput and latency characteristics, analyze the "
        "tradeoffs in a comparison table, and write a short recommendation. Use "
        "reasonable assumptions; do not ask clarifying questions."
    )
    run_id = "smoke-p1"
    config = {
        "configurable": {"thread_id": run_id},
        "tags": [f"tier_req:{os.environ['ADL_PLANNER_TIER']}"],
        "recursion_limit": 80,
    }

    # Capture delegations from the stream, keyed by the task tool_call id so partial
    # streaming chunks (subagent_type briefly None before args accumulate) and any
    # duplicate emissions collapse to one entry per real delegation. P1 validates the
    # PLAN shape; worker execution robustness (slow cold-CPU tiers vs the gateway's
    # 180s upstream timeout) is a Stage-2 concern, so a worker error here does not
    # fail the decomposition gate.
    delegations: dict[str, str | None] = {}
    async with AsyncSqliteSaver.from_conn_string(":memory:") as cp:
        agent = build_agent(cp, mcp_tools=[], tools_by_name=None)
        try:
            async for _mode, chunk in agent.astream(
                {"messages": objective}, config, stream_mode=["updates"]
            ):
                for upd in (chunk or {}).values():
                    for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                        for tc in getattr(m, "tool_calls", None) or []:
                            if tc.get("name") == "task" and tc.get("id"):
                                sub = (tc.get("args") or {}).get("subagent_type")
                                if sub or tc["id"] not in delegations:
                                    delegations[tc["id"]] = sub
        except Exception as e:  # noqa: BLE001 — worker exec failure must not fail P1
            print(f"  (worker execution error, ignored for P1): {type(e).__name__}: {str(e)[:90]}")

    targets = [t for t in delegations.values() if t]
    print(f"  delegations (by task id): {targets}")
    n = len(targets)
    known = all(s in profile_names for s in targets)
    ok = 2 <= n <= 5 and known
    if not (2 <= n <= 5):
        print(f"  FAIL: expected 2..5 subtask delegations to named profiles, saw {n}")
    if not known:
        bad = [s for s in targets if s not in profile_names]
        print(f"  FAIL: delegation targets unknown/hand-authored agents: {bad}")
    return ok


def main() -> int:
    if os.environ.get("SR_AUTH_MODE") == "proxy" and not os.environ.get("SR_PROXY_SECRET"):
        print("ERROR: gateway is in proxy mode but SR_PROXY_SECRET is unset. "
              "Export SR_AUTH_EMAIL + SR_PROXY_SECRET (or restart the gateway in open mode).")
        return 2

    _hr("P0 — ModelFactory tier round-trip through the gateway")
    p0 = p0_model_factory()
    print(f"  -> P0 {'PASS' if p0 else 'FAIL'}")

    _hr("P1 — Auto-decomposition into profile-bound subtasks")
    try:
        p1 = asyncio.run(p1_decomposition())
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL: {type(e).__name__}: {str(e)[:200]}")
        p1 = False
    print(f"  -> P1 {'PASS' if p1 else 'FAIL'}")

    _hr("RESULT")
    print(f"  P0={'PASS' if p0 else 'FAIL'}  P1={'PASS' if p1 else 'FAIL'}")
    return 0 if (p0 and p1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
