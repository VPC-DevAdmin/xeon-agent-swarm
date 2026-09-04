"""Per-run stage accounting on the executor.

Every stage the retrieval and sandbox code times (`retrieval._note`) is
also added to the accumulator of the run that is executing, found through
a context variable set when the run's task starts (`begin`), so it follows
the run into the worker tasks and threads it spawns. At finalize the
adapter collects the sums into the run's metrics; the control process
carries them into the unit's trace and the evidence ledger writes them on
the unit row. A mixed plateau can then be split by archetype and stage
without inference from fleet-wide stage statistics.

The sums are RESOURCE TIME per unit (a researcher's three workers retrieve
in parallel, so its retrieval sum is three retrievals, not the critical
path). Compare a stage's per-unit sum across rates to see which stage a
given archetype's slowdown lives in."""
from __future__ import annotations

import contextvars

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "capacity_run_id", default=None)
_acc: dict[str, dict[str, list[float]]] = {}


def begin(run_id: str) -> None:
    """Bind the current context to `run_id` and open its accumulator."""
    _run_id.set(run_id)
    _acc[run_id] = {}


def current() -> str | None:
    return _run_id.get()


def note(stage: str, ms: float) -> None:
    """Add one timed stage to the executing run, if any."""
    rid = _run_id.get()
    if rid is None:
        return
    d = _acc.get(rid)
    if d is None:
        return
    e = d.setdefault(stage, [0.0, 0.0])
    e[0] += float(ms)
    e[1] += 1.0


def collect(run_id: str) -> dict[str, dict]:
    """Close the run's accumulator and return {stage: {ms, n}}."""
    d = _acc.pop(run_id, None) or {}
    return {k: {"ms": round(v[0], 1), "n": int(v[1])} for k, v in d.items()}
