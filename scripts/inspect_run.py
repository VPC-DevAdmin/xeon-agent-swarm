#!/usr/bin/env python3
"""
Fetch a completed run and print each task's objective, status, and raw output.

Usage:
    python3 scripts/inspect_run.py <run_id>
    python3 scripts/inspect_run.py <run_id> --url http://host:8000
    python3 scripts/inspect_run.py <run_id> --full        # no truncation
    python3 scripts/inspect_run.py <run_id> --task t3     # just that task

A "run_id" can be the short prefix shown in the dashboard header
(e.g. 80bde294) — this script will fetch /runs and match the prefix.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"
CYAN  = "\033[36m"

STATUS_COLOUR = {
    "completed": GREEN,
    "approved":  GREEN,
    "failed":    RED,
    "killed":    RED,
    "rejected_final": YELLOW,
}


def _fetch(url: str, run_id: str) -> dict:
    # Try direct first
    try:
        with urllib.request.urlopen(f"{url}/run/{run_id}", timeout=15) as r:
            data = json.loads(r.read())
        # If the backend returned {"status": "not_found"}, fall through to search
        if data.get("status") == "not_found":
            raise ValueError("direct fetch returned not_found")
        return data
    except Exception:
        pass

    print(f"{DIM}Direct fetch failed or returned not_found — nothing else to try yet.{RESET}",
          file=sys.stderr)
    print(f"{RED}Run {run_id} not found.{RESET}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("run_id", help="Full run_id UUID (dashboard shows first 8 chars)")
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--full", action="store_true",
                   help="Print task outputs without truncation")
    p.add_argument("--task", help="Filter to one task id (e.g. t3)")
    p.add_argument("--limit", type=int, default=2500,
                   help="Chars per task output (default 2500; ignored with --full)")
    args = p.parse_args()

    data = _fetch(args.url, args.run_id)
    swarm   = data.get("swarm", {}) or {}
    tg      = swarm.get("task_graph", {}) or {}
    tasks   = {t["id"]: t for t in tg.get("tasks", [])}
    results = swarm.get("results", {}) or {}

    doc = (data.get("document") or {})
    print(f"{BOLD}Run{RESET}        {data.get('run_id','?')}")
    print(f"{BOLD}Query{RESET}      {(data.get('query') or '—')[:200]}")
    print(f"{BOLD}Tasks{RESET}      {len(tasks)} planned  {len(results)} with results")
    if doc:
        print(f"{BOLD}Document{RESET}   title={doc.get('title','—')[:80]!r}")
    print()

    shown = 0
    for tid in sorted(results):
        if args.task and tid != args.task:
            continue
        r = results[tid]
        t = tasks.get(tid, {})
        status = r.get("status", "?")
        colour = STATUS_COLOUR.get(status, "")

        print("═" * 88)
        print(f"{colour}{BOLD}── {tid}  [{status}]{RESET}  "
              f"{t.get('type','?')}  conf={r.get('confidence',0):.2f}  "
              f"model={r.get('model_used','?')}  "
              f"latency={r.get('latency_ms',0):.0f}ms")
        print(f"{CYAN}OBJ{RESET}   {(t.get('objective') or t.get('description',''))[:200]}")
        print(f"{CYAN}FMT{RESET}   {t.get('deliverable_format','—')}")
        deps = t.get("dependencies") or []
        if deps:
            print(f"{CYAN}DEPS{RESET}  {', '.join(deps)}")
        print()
        result = r.get("result", "")
        if isinstance(result, (dict, list)):
            result = json.dumps(result, indent=2, default=str)
        result = str(result)
        if not args.full and len(result) > args.limit:
            result = result[:args.limit] + f"\n{DIM}… [truncated, {len(r.get('result','')) - args.limit} more chars]{RESET}"
        print(result)
        print()
        shown += 1

    if not shown:
        print(f"{RED}No tasks matched{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
