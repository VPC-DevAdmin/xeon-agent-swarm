#!/usr/bin/env python3
"""
Command-line swarm test runner.

Usage:
    python3 scripts/test_run.py "your query here"
    python3 scripts/test_run.py "your query" --validator
    python3 scripts/test_run.py "your query" --url http://localhost:8000

Streams all swarm events to stdout in real time, then prints the
final DocumentResult summary when the run completes.

Requires: pip install websockets
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
import time
import urllib.request
from datetime import datetime


# ── ANSI colours ────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAGENTA = "\033[35m"

EVENT_COLOURS = {
    "run_started":          CYAN,
    "orchestration_done":   BLUE,
    "graph_valid":          GREEN,
    "graph_invalid":        RED,
    "task_started":         YELLOW,
    "task_completed":       GREEN,
    "task_failed":          RED,
    "task_killed":          RED,
    "validator_started":    MAGENTA,
    "validator_approved":   GREEN,
    "validator_rejected":   YELLOW,
    "worker_retrying":      YELLOW,
    "worker_rejected_final": RED,
    "tts_started":          DIM,
    "tts_completed":        DIM,
    "run_completed":        BOLD + GREEN,
    "run_metrics":          CYAN,
    "reduction_started":    BLUE,
    "reduction_done":       BLUE,
}


def _colour(event_type: str, text: str) -> str:
    c = EVENT_COLOURS.get(event_type, "")
    return f"{c}{text}{RESET}" if c else text


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _wrap(text: str, width: int = 100, indent: str = "    ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent, break_long_words=False)


def _print_event(evt: dict) -> None:
    etype  = evt.get("event", "unknown")
    payload = evt.get("payload", {}) or {}

    prefix = _colour(etype, f"[{_ts()}] {etype:<28}")

    # ── Special formatting per event type ───────────────────────────────────
    if etype == "run_started":
        print(f"{prefix}  run_id={evt.get('run_id','?')}")

    elif etype == "orchestration_done":
        tasks = payload.get("task_graph", {}).get("tasks", [])
        print(f"{prefix}  {len(tasks)} tasks planned")
        for t in tasks:
            deps = ", ".join(t.get("dependencies", [])) or "—"
            fmt  = t.get("deliverable_format", "")
            obj  = (t.get("objective") or t.get("description", ""))[:70]
            print(f"    {DIM}{t['id']:<12} [{t['type']:<10}]  deps={deps:<20} fmt={fmt}{RESET}")
            if obj:
                print(f"             {DIM}{obj}{RESET}")

    elif etype in ("task_started",):
        tid  = payload.get("task_id", "?")
        ttype = payload.get("task_type", "")
        obj   = payload.get("objective") or payload.get("description", "")
        print(f"{prefix}  {tid}  ({ttype})")
        if obj:
            print(_wrap(obj[:120]))

    elif etype in ("task_completed",):
        tid     = payload.get("task_id", "?")
        latency = payload.get("latency_ms", 0)
        conf    = payload.get("confidence", 0)
        snippet = str(payload.get("result", ""))[:160].replace("\n", " ")
        print(f"{prefix}  {tid}  {latency:.0f}ms  conf={conf:.2f}")
        if snippet:
            print(_wrap(snippet))

    elif etype in ("task_failed", "task_killed"):
        tid = payload.get("task_id", "?")
        err = str(payload.get("error", payload.get("result", "")))[:200]
        print(f"{prefix}  {tid}")
        if err:
            print(_wrap(err))

    elif etype == "validator_started":
        print(f"{prefix}  task={payload.get('task_id','?')}  attempt={payload.get('attempt',1)}")

    elif etype == "validator_approved":
        print(f"{prefix}  task={payload.get('task_id','?')}")

    elif etype == "validator_rejected":
        tid  = payload.get("task_id", "?")
        hint = payload.get("correction_hint", "")[:120]
        print(f"{prefix}  task={tid}")
        if hint:
            print(_wrap(f"hint: {hint}"))

    elif etype == "worker_retrying":
        print(f"{prefix}  task={payload.get('task_id','?')}  attempt={payload.get('attempt',1)}")

    elif etype == "worker_rejected_final":
        print(f"{prefix}  task={payload.get('task_id','?')}")

    elif etype == "run_metrics":
        m = payload
        print(f"{prefix}")
        print(f"    tasks={m.get('total_tasks',0)}  attempts={m.get('total_attempts',0)}"
              f"  retries={m.get('total_retries',0)}"
              f"  wall={m.get('wall_clock_ms',0)/1000:.1f}s")
        vrun  = m.get('validations_run', 0)
        vpass = m.get('validations_passed', 0)
        if vrun:
            print(f"    validations={vpass}/{vrun} passed"
                  f"  tokens={m.get('total_tokens_in',0)+m.get('total_tokens_out',0)}")

    elif etype == "run_completed":
        print(f"{prefix}")

    elif etype in ("reduction_started", "reduction_done",
                   "tts_started", "tts_completed",
                   "graph_valid", "graph_invalid"):
        detail = str(payload)[:120] if payload else ""
        print(f"{prefix}  {detail}")

    else:
        # Fallback: dump payload keys
        keys = ", ".join(f"{k}={str(v)[:40]}" for k, v in payload.items()) if payload else ""
        print(f"{prefix}  {keys}")


def _print_result(result: dict) -> None:
    doc = result.get("document")
    if not doc:
        print(f"\n{BOLD}── Run result (no document produced) ──{RESET}")
        print(json.dumps(result, indent=2, default=str)[:2000])
        return

    print(f"\n{BOLD}{'═'*80}{RESET}")
    print(f"{BOLD}REPORT: {doc.get('title','(untitled)')}{RESET}")
    print(f"{'═'*80}")

    summary = doc.get("executive_summary", "")
    if summary:
        print(f"\n{BOLD}Executive Summary{RESET}")
        print(_wrap(summary, width=100, indent="  "))

    sections = doc.get("sections", [])
    for i, sec in enumerate(sections, 1):
        print(f"\n{BOLD}§{i}  {sec.get('title','')}{RESET}")
        body = sec.get("content", "")
        # Print first 600 chars of each section
        snippet = body[:600]
        if len(body) > 600:
            snippet += f"\n{DIM}  … ({len(body)-600} more chars){RESET}"
        print(_wrap(snippet, width=100, indent="  "))

    refs = doc.get("references", [])
    if refs:
        print(f"\n{BOLD}References ({len(refs)}){RESET}")
        for r in refs[:8]:
            print(f"  • {r}")
        if len(refs) > 8:
            print(f"  {DIM}… {len(refs)-8} more{RESET}")

    print(f"\n{'─'*80}")


async def _stream_ws(url: str, run_id: str) -> bool:
    """Stream WebSocket events. Returns True if run_completed was received."""
    try:
        import websockets  # type: ignore
    except ImportError:
        print(f"\n{RED}websockets not installed. Run:  pip install websockets{RESET}", file=sys.stderr)
        sys.exit(1)

    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/ws/{run_id}"
    print(f"{DIM}Connecting to {ws_url}{RESET}\n")

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=60,
                                   open_timeout=10) as ws:
        async for raw in ws:
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                print(f"{DIM}[raw] {raw[:200]}{RESET}")
                continue

            _print_event(evt)

            if evt.get("event") == "run_completed":
                return True

    return False


def _post_run(url: str, query: str, validator: bool) -> str:
    body = json.dumps({"query": query, "validator_enabled": validator}).encode()
    req  = urllib.request.Request(
        f"{url}/run",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["run_id"]


def _fetch_result(url: str, run_id: str) -> dict:
    with urllib.request.urlopen(f"{url}/run/{run_id}", timeout=30) as resp:
        return json.loads(resp.read())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a swarm run end-to-end")
    parser.add_argument("query", help="The research query to run")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--validator", action="store_true",
                        help="Enable contract enforcement / validator retries")
    args = parser.parse_args()

    print(f"\n{BOLD}Query:{RESET} {args.query}")
    print(f"{BOLD}URL:  {RESET} {args.url}")
    print(f"{BOLD}Validator:{RESET} {'on' if args.validator else 'off'}\n")

    t0 = time.time()

    run_id = _post_run(args.url, args.query, args.validator)
    print(f"{BOLD}run_id:{RESET} {run_id}\n{'─'*80}\n")

    completed = await _stream_ws(args.url, run_id)

    elapsed = time.time() - t0
    print(f"\n{DIM}Stream ended after {elapsed:.1f}s  (completed={completed}){RESET}")

    # Fetch and display final result
    result = _fetch_result(args.url, run_id)
    _print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
