#!/usr/bin/env python3
"""
Live terminal dashboard for a swarm run.

Shows a task grid (each agent as a row with status / attempts / latency),
a rolling event log, and metrics — all updating in real time. When the run
completes, the final report is rendered below the dashboard.

Usage:
    python3 scripts/dashboard.py "your query"
    python3 scripts/dashboard.py "your query" --validator
    python3 scripts/dashboard.py "your query" --url http://host:8000

Dependencies:
    pip install websockets rich
    # or on Debian/Ubuntu:
    sudo apt install python3-websockets python3-rich
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


try:
    import websockets  # type: ignore
except ImportError:
    print("Missing dependency: websockets\n"
          "  sudo apt install python3-websockets\n"
          "  # or: pip install websockets", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.markdown import Markdown
    from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
except ImportError:
    print("Missing dependency: rich\n"
          "  sudo apt install python3-rich\n"
          "  # or: pip install rich", file=sys.stderr)
    sys.exit(1)


console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Task state
# ─────────────────────────────────────────────────────────────────────────────

STATUS_STYLE = {
    "pending":           ("dim",         "⋯"),
    "running":           ("yellow",      "▶"),
    "validating":        ("magenta",     "?"),
    "retrying":          ("orange3",     "↻"),
    "approved":          ("green",       "✓"),
    "rejected_final":    ("red",         "!"),
    "done":              ("bold green",  "✓"),
    "failed":            ("bold red",    "✗"),
    "killed":            ("red",         "⊗"),
}


@dataclass
class TaskState:
    id: str
    type: str = ""
    objective: str = ""
    deliverable_format: str = ""
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    latency_ms: float = 0.0
    confidence: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    last_event: str = ""
    correction_hint: str = ""

    @property
    def elapsed(self) -> str:
        if self.status == "done" and self.completed_at:
            return f"{(self.completed_at - self.started_at):.1f}s"
        if self.started_at and self.status in ("running", "validating", "retrying"):
            return f"{(time.time() - self.started_at):.1f}s"
        return "—"


@dataclass
class RunState:
    run_id: str = ""
    query: str = ""
    validator_enabled: bool = False
    started_at: float = field(default_factory=time.time)
    completed: bool = False
    tasks: dict[str, TaskState] = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=18))
    metrics: dict[str, Any] = field(default_factory=dict)
    orchestration_retries: int = 0
    graph_valid: bool | None = None
    reduction_active: bool = False
    tts_active: bool = False
    final_result: dict | None = None

    def task_order(self) -> list[TaskState]:
        # Dependencies first, then alphabetical
        return sorted(
            self.tasks.values(),
            key=lambda t: (len(t.dependencies), t.id),
        )

    def counts(self) -> dict[str, int]:
        c = {"pending": 0, "running": 0, "validating": 0,
             "retrying": 0, "done": 0, "failed": 0, "killed": 0}
        for t in self.tasks.values():
            c[t.status if t.status in c else "running"] = c.get(
                t.status if t.status in c else "running", 0) + 1
        return c


# ─────────────────────────────────────────────────────────────────────────────
# Event handling
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(state: RunState, etype: str, detail: str) -> None:
    colour_map = {
        "task_started":          "yellow",
        "task_completed":        "green",
        "task_failed":           "red",
        "task_killed":           "red",
        "validator_started":     "magenta",
        "validator_approved":    "green",
        "validator_rejected":    "orange3",
        "worker_retrying":       "orange3",
        "worker_rejected_final": "red",
        "orchestration_done":    "cyan",
        "graph_valid":           "green",
        "graph_invalid":         "red",
        "run_started":           "cyan",
        "run_completed":         "bold green",
        "run_metrics":           "cyan",
        "reduction_started":     "blue",
        "reduction_done":        "blue",
        "tts_started":           "dim",
        "tts_completed":         "dim",
    }
    colour = colour_map.get(etype, "white")
    state.events.appendleft(
        f"[dim]{_ts()}[/dim] [{colour}]{etype:<22}[/{colour}] {detail}"
    )


def apply_event(state: RunState, evt: dict) -> None:
    etype   = evt.get("event", "")
    payload = evt.get("payload", {}) or {}

    if etype == "run_started":
        state.run_id = evt.get("run_id", state.run_id)
        _log(state, etype, f"run_id={state.run_id[:8]}")

    elif etype == "orchestration_done":
        tasks = payload.get("task_graph", {}).get("tasks", [])
        # Preserve any existing task state on re-orchestration (retries)
        new_tasks: dict[str, TaskState] = {}
        for t in tasks:
            tid = t["id"]
            existing = state.tasks.get(tid)
            new_tasks[tid] = existing or TaskState(id=tid)
            new_tasks[tid].type = t.get("type", "")
            new_tasks[tid].objective = (t.get("objective")
                                         or t.get("description", ""))
            new_tasks[tid].deliverable_format = t.get("deliverable_format", "")
            new_tasks[tid].dependencies = t.get("dependencies", [])
        state.tasks = new_tasks
        _log(state, etype, f"{len(tasks)} tasks planned")

    elif etype == "graph_valid":
        state.graph_valid = True
        _log(state, etype, "dependency check passed")

    elif etype == "graph_invalid":
        state.graph_valid = False
        errs = payload.get("errors", [])
        _log(state, etype, f"{len(errs)} errors — retrying orchestration")
        state.orchestration_retries += 1

    elif etype == "task_started":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.type = payload.get("task_type", t.type)
        t.status = "running"
        t.started_at = time.time()
        t.attempts = max(t.attempts, 1)
        _log(state, etype, f"{tid} ({t.type})")

    elif etype == "validator_started":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "validating"
        _log(state, etype, f"{tid} attempt={payload.get('attempt',1)}")

    elif etype == "validator_approved":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "approved"
        _log(state, etype, f"{tid}")

    elif etype == "validator_rejected":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "retrying"
        t.correction_hint = (payload.get("correction_hint", "") or "")[:80]
        _log(state, etype, f"{tid}: {t.correction_hint[:60]}")

    elif etype == "worker_retrying":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "retrying"
        t.attempts = int(payload.get("attempt", t.attempts + 1))
        _log(state, etype, f"{tid} attempt {t.attempts}")

    elif etype == "worker_rejected_final":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "rejected_final"
        _log(state, etype, f"{tid} committed with warnings")

    elif etype == "task_completed":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        if t.status != "rejected_final":
            t.status = "done"
        t.completed_at = time.time()
        t.latency_ms = float(payload.get("latency_ms", 0))
        t.confidence = float(payload.get("confidence", 0))
        _log(state, etype, f"{tid} {t.latency_ms:.0f}ms conf={t.confidence:.2f}")

    elif etype == "task_failed":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "failed"
        t.completed_at = time.time()
        err = str(payload.get("error", payload.get("result", "")))[:80]
        _log(state, etype, f"{tid}: {err}")

    elif etype == "task_killed":
        tid = payload.get("task_id", "")
        t = state.tasks.setdefault(tid, TaskState(id=tid))
        t.status = "killed"
        t.completed_at = time.time()
        _log(state, etype, f"{tid}")

    elif etype == "reduction_started":
        state.reduction_active = True
        _log(state, etype, "synthesizing report")

    elif etype == "reduction_done":
        state.reduction_active = False
        _log(state, etype, "report ready")

    elif etype == "tts_started":
        state.tts_active = True
        _log(state, etype, payload.get("section", ""))

    elif etype == "tts_completed":
        state.tts_active = False
        _log(state, etype, payload.get("section", ""))

    elif etype == "run_metrics":
        state.metrics = payload
        _log(state, etype, f"tasks={payload.get('total_tasks',0)} "
                            f"retries={payload.get('total_retries',0)}")

    elif etype == "run_completed":
        state.completed = True
        _log(state, etype, "")

    else:
        _log(state, etype, "")


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_header(state: RunState) -> Panel:
    elapsed = time.time() - state.started_at
    q = state.query[:160] + ("…" if len(state.query) > 160 else "")
    vinfo = "[green]ON[/green]" if state.validator_enabled else "[dim]off[/dim]"
    status = ("[bold green]COMPLETE[/bold green]" if state.completed
              else "[bold yellow]RUNNING[/bold yellow]")
    gv = ("[green]✓[/green]" if state.graph_valid is True else
          "[red]✗[/red]" if state.graph_valid is False else "—")
    retry_info = (f" [dim](orchestrator retries: {state.orchestration_retries})[/dim]"
                  if state.orchestration_retries else "")

    text = Text.from_markup(
        f"{status}   validator={vinfo}   graph={gv}   "
        f"elapsed=[cyan]{elapsed:.1f}s[/cyan]   "
        f"run_id=[dim]{state.run_id[:8] if state.run_id else '—'}[/dim]"
        f"{retry_info}\n"
        f"[dim]query:[/dim] {q}"
    )
    return Panel(text, title="Xeon Agent Swarm", border_style="cyan")


def render_task_grid(state: RunState) -> Panel:
    table = Table(expand=True, show_lines=False, pad_edge=False, box=None)
    table.add_column("", width=1, no_wrap=True)
    table.add_column("ID", style="cyan", width=14, no_wrap=True)
    table.add_column("Type", style="blue", width=11, no_wrap=True)
    table.add_column("Status", width=14, no_wrap=True)
    table.add_column("Try", justify="right", width=3)
    table.add_column("Elapsed", justify="right", width=7, no_wrap=True)
    table.add_column("Conf", justify="right", width=5, no_wrap=True)
    table.add_column("Objective", ratio=1)

    if not state.tasks:
        table.add_row("", "[dim]waiting for orchestration…[/dim]",
                      "", "", "", "", "", "")
    else:
        for t in state.task_order():
            style, glyph = STATUS_STYLE.get(t.status, ("white", "?"))
            conf_str = f"{t.confidence:.2f}" if t.confidence else "—"
            obj_text = t.objective[:90]
            if t.correction_hint and t.status == "retrying":
                obj_text = f"[orange3]hint: {t.correction_hint[:60]}[/orange3]"
            table.add_row(
                f"[{style}]{glyph}[/{style}]",
                t.id,
                t.type or "—",
                f"[{style}]{t.status}[/{style}]",
                str(t.attempts) if t.attempts else "—",
                t.elapsed,
                conf_str,
                obj_text,
            )

    # Add reduction / TTS indicators as pseudo-rows
    if state.reduction_active:
        table.add_row("[blue]▶[/blue]", "[blue]reducer[/blue]", "writing",
                      "[blue]synthesizing[/blue]", "—", "—", "—",
                      "assembling document from worker outputs")
    if state.tts_active:
        table.add_row("[dim]▶[/dim]", "[dim]tts[/dim]", "audio",
                      "[dim]rendering[/dim]", "—", "—", "—",
                      "generating executive summary audio")

    return Panel(table, title="Agents", border_style="yellow")


def render_events(state: RunState) -> Panel:
    if not state.events:
        body = Text.from_markup("[dim]waiting for events…[/dim]")
    else:
        body = Text.from_markup("\n".join(state.events))
    return Panel(body, title="Event log", border_style="magenta")


def render_metrics(state: RunState) -> Panel:
    counts = state.counts()
    m = state.metrics

    lines: list[str] = []
    # Task status summary
    lines.append(
        f"[dim]tasks:[/dim] "
        f"[green]{counts['done']} done[/green]  "
        f"[yellow]{counts['running']} running[/yellow]  "
        f"[magenta]{counts['validating']} validating[/magenta]  "
        f"[orange3]{counts['retrying']} retrying[/orange3]  "
        f"[dim]{counts['pending']} pending[/dim]"
    )
    if counts["failed"] or counts["killed"]:
        lines.append(
            f"[red]{counts['failed']} failed  {counts['killed']} killed[/red]"
        )

    # Final metrics (appear at run_completed)
    if m:
        vrun  = m.get("validations_run", 0)
        vpass = m.get("validations_passed", 0)
        pct   = (vpass / vrun * 100) if vrun else 0
        wall  = m.get("wall_clock_ms", 0) / 1000
        tok_in = m.get("total_tokens_in", 0)
        tok_out = m.get("total_tokens_out", 0)
        tok_val = m.get("total_tokens_validator", 0)
        tok_tot = tok_in + tok_out + tok_val

        lines.append("")
        lines.append(f"[bold]Final metrics[/bold]")
        lines.append(
            f"  attempts=[cyan]{m.get('total_attempts',0)}[/cyan]  "
            f"retries=[orange3]{m.get('total_retries',0)}[/orange3]  "
            f"wall=[cyan]{wall:.1f}s[/cyan]"
        )
        if vrun:
            colour = "green" if vpass == vrun else "yellow"
            lines.append(
                f"  validator pass=[{colour}]{vpass}/{vrun} ({pct:.0f}%)[/{colour}]  "
                f"committed-with-warnings=[red]"
                f"{m.get('workers_rejected_committed',0)}[/red]"
            )
        if tok_tot:
            vpct = (tok_val / tok_tot * 100) if tok_tot else 0
            lines.append(
                f"  tokens=[cyan]{tok_tot:,}[/cyan] "
                f"(in=[dim]{tok_in:,}[/dim] out=[dim]{tok_out:,}[/dim] "
                f"validator=[dim]{tok_val:,} ({vpct:.0f}%)[/dim])"
            )

    body = Text.from_markup("\n".join(lines))
    return Panel(body, title="Metrics", border_style="cyan")


def build_layout(state: RunState) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(render_header(state), name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(render_metrics(state), name="metrics", size=8),
    )
    layout["body"].split_row(
        Layout(render_task_grid(state), name="tasks", ratio=2),
        Layout(render_events(state), name="events", ratio=1),
    )
    return layout


# ─────────────────────────────────────────────────────────────────────────────
# Final report rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_final_report(result: dict) -> None:
    doc = result.get("document") if result else None
    if not doc:
        console.print("\n[yellow]No document in final result.[/yellow]")
        console.print_json(data=result or {}, default=str)
        return

    console.rule("[bold]Final Report[/bold]")

    console.print(Panel(
        f"[bold]{doc.get('title','(untitled)')}[/bold]",
        border_style="green",
    ))

    summary = doc.get("executive_summary", "")
    if summary:
        console.print(Panel(Markdown(summary),
                            title="Executive Summary", border_style="cyan"))

    sections = doc.get("sections", [])
    for i, sec in enumerate(sections, 1):
        title = sec.get("title", f"Section {i}")
        body = sec.get("content", "")
        if len(body) > 2000:
            body = body[:2000] + "\n\n*…truncated…*"
        console.print(Panel(Markdown(body),
                            title=f"§{i}  {title}",
                            border_style="blue"))

    refs = doc.get("references", [])
    if refs:
        ref_text = "\n".join(f"- {r}" for r in refs[:20])
        if len(refs) > 20:
            ref_text += f"\n- … {len(refs) - 20} more"
        console.print(Panel(Markdown(ref_text),
                            title=f"References ({len(refs)})",
                            border_style="dim"))


# ─────────────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────────────

def _post_run(url: str, query: str, validator: bool) -> str:
    body = json.dumps({
        "query": query,
        "validator_enabled": validator,
    }).encode()
    req = urllib.request.Request(
        f"{url}/run", data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["run_id"]


def _fetch_result(url: str, run_id: str) -> dict:
    with urllib.request.urlopen(f"{url}/run/{run_id}", timeout=30) as resp:
        return json.loads(resp.read())


async def run_dashboard(url: str, query: str, validator: bool) -> None:
    state = RunState(query=query, validator_enabled=validator)
    state.run_id = _post_run(url, query, validator)

    ws_url = (url.replace("http://", "ws://")
                 .replace("https://", "wss://") + f"/ws/{state.run_id}")

    # Interval refresh (so elapsed timers tick even without events)
    refresh_per_second = 4

    with Live(build_layout(state), console=console,
              refresh_per_second=refresh_per_second, screen=False,
              transient=False) as live:

        async def _refresher():
            while not state.completed:
                live.update(build_layout(state))
                await asyncio.sleep(1.0 / refresh_per_second)

        async def _stream():
            async with websockets.connect(
                ws_url, ping_interval=20, ping_timeout=60, open_timeout=15,
            ) as ws:
                async for raw in ws:
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    apply_event(state, evt)
                    live.update(build_layout(state))
                    if state.completed:
                        break

        await asyncio.gather(_refresher(), _stream())
        # One final render
        live.update(build_layout(state))

    # Fetch and render final report outside the Live region
    try:
        state.final_result = _fetch_result(url, state.run_id)
    except Exception as e:
        console.print(f"\n[red]Failed to fetch final result: {e}[/red]")
        return

    render_final_report(state.final_result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live terminal dashboard for a swarm run",
    )
    parser.add_argument("query", help="Research query to run")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--validator", action="store_true",
                        help="Enable contract enforcement / validator retries")
    args = parser.parse_args()

    try:
        asyncio.run(run_dashboard(args.url, args.query, args.validator))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
