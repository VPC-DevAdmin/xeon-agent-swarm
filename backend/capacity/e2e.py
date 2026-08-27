"""
End-to-end agent-runtime execution for the capacity tester.

One "call" here is one COMPLETE workflow through the real orchestrator:
main.launch_run -> deepagents planner -> workers -> validation -> synthesis ->
durable Run record. We await the run's own asyncio task (same process, same
loop), then read the durable record for the outcome, token totals, and the
request trace (LLM calls, steps, validations) — the trace is what lets the
synthetic profiles be calibrated against reality.

Determinism note: workers/tools behave exactly as the app is configured —
against the mock router the whole workflow is deterministic and free; against
the live router this is the truth test and each workflow spends real routing.
"""
from __future__ import annotations

import asyncio
import time


class E2ERunner:
    """submit() is injectable for tests; the default drives the real engine."""

    def __init__(self, timeout_s: float = 300.0, submit=None, *,
                 router_base_url: str | None = None,
                 router_api_key: str | None = None,
                 router_model: str | None = None,
                 router_provider: str = "openai"):
        self.timeout_s = float(timeout_s)
        self._submit = submit or self._real_submit
        self.router_base_url = router_base_url
        self.router_api_key = router_api_key
        self.router_model = router_model
        self.router_provider = router_provider

    async def run_workflow(self, wid: str, query: str, opts: dict | None = None,
                           timeout_s: float | None = None) -> dict:
        t0 = time.perf_counter()
        limit = float(timeout_s) if timeout_s is not None else self.timeout_s
        try:
            out = await asyncio.wait_for(self._submit(query, opts or {}),
                                         timeout=limit)
        except asyncio.TimeoutError:
            return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": 0, "tokens_out": 0,
                    "error": f"workflow timeout after {limit}s"}
        except Exception as exc:  # noqa: BLE001 — a failed workflow is a data point
            return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "tokens_in": 0, "tokens_out": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:160]}
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    async def _real_submit(self, query: str, opts: dict | None = None) -> dict:
        opts = opts or {}
        # Lazy imports: main imports the capacity router at startup — importing
        # main at module level here would be circular.
        from backend import main as app_main
        from backend.db.base import get_sessionmaker
        from backend.repositories import runs as runs_repo

        run_id = app_main.launch_run(
            query,
            validator_enabled=bool(opts.get("validator_enabled", True)),
            trigger="api", plan_approval=False,  # benchmarks never pause for HITL
            enabled_tools=list(opts.get("enabled_tools") or []),
            budget=opts.get("budgets") or None,
            toolless=bool(opts.get("toolless", False)),
            router_base_url=self.router_base_url,
            router_api_key=self.router_api_key,
            router_model=self.router_model,
            router_provider=self.router_provider)
        task = app_main._run_tasks.get(run_id)
        if task is None:
            # Dispatched to an executor: await its completion callback — the
            # outcome arrives with the terminal state, no DB polling. The outer
            # wait_for bounds the wait.
            return await app_main.wait_outcome(run_id)
        await asyncio.shield(asyncio.wait({task}))
        # In-process run: the task finished; writes ride the batched writer, so
        # barrier before reading the durable outcome.
        from backend.repositories import persistence
        await persistence.barrier()
        while True:
            sm = get_sessionmaker()
            async with sm() as session:
                run = await runs_repo.get_run(session, run_id)
            if run is not None and run.status not in ("pending", "running",
                                                       "awaiting_approval"):
                m = run.metrics or {}
                steps = [s for s in run.steps if s.step_key != "orchestrator"]
                validations = sum(len(s.validations) for s in run.steps)
                tokens_out = int(m.get("tokens_out") or 0)
                total = int(m.get("total_tokens") or 0)
                # A budget stop means the fixed-size work unit did NOT complete
                # — partial synthesis salvages an answer for the user, but for
                # the benchmark it is a failure, exactly like a timeout.
                budget_hit = m.get("budget_exceeded")
                ok = run.status == "completed" and not budget_hit
                if budget_hit:
                    err = (f"budget exceeded: {budget_hit.get('kind')} "
                           f"{budget_hit.get('used')} > {budget_hit.get('limit')}")
                elif run.status == "completed":
                    err = None
                else:
                    err = (f"status={run.status}"
                           + (f" — {run.error}" if run.error else ""))[:300]
                return {
                    "ok": ok,
                    # The outcome was read back from the durable record after a
                    # write barrier, so a clean unit is a committed unit.
                    "durable": True,
                    "tokens_in": max(0, total - tokens_out),
                    "tokens_out": tokens_out,
                    "run_id": run_id,
                    "error": err,
                    # trace: the real request shape, for calibrating synthetics
                    "trace": {
                        "llm_calls": int(m.get("call_count") or 0),
                        "steps": len(steps),
                        "validations": validations,
                        "task_count": int(m.get("task_count") or 0),
                        "tool_calls": int(m.get("tool_calls") or 0),
                    },
                }
            await asyncio.sleep(0.5)
