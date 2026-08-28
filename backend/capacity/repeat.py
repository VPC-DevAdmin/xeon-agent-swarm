"""
Repeat-run driver: the difference between a number and a reportable number.

One benchmark run is one sample. Agent workloads are noisy — seeded think-time
jitter, garbage collection, page cache, whatever else shares the box — so a
single figure carries an unknown error bar and cannot honestly be compared
against another single figure. This runs the same benchmark N times under
different seeds and reports the median with the observed range.

Two rules keep a set honest.

  comparability   Every run in a set must share its workload fingerprint, its
                  commit, its target, its inference backend, its mix, and its
                  load model. A redeploy between run two and run three would
                  otherwise produce a "range" that is really two different
                  benchmarks blended together. A mismatch ends the set rather
                  than being averaged over, because no retry can undo it.
  exclusion       A run contaminated by other tenants on the box, or one that
                  failed its own workload or harness integrity check, is not a
                  sample. It is recorded with its reason and retried. When the
                  retries run out the set reports itself INCOMPLETE instead of
                  publishing a median over whatever happened to survive.

A censored run still counts as a sample — it measured something real — but it
makes the whole set a lower bound, and the aggregate says so.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time

from backend.capacity import controller as ctl
from backend.capacity.telemetry import SystemSampler

logger = logging.getLogger(__name__)

DEFAULT_RUNS = 3
DEFAULT_SETTLE_S = 60.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_QUIET_CPU_PCT = 25.0
DEFAULT_QUIET_TIMEOUT_S = 300.0

# Runs that measured nothing usable. Interference is separate from the invalid
# verdicts: the run was fine, the box was not.
EXCLUDE_VERDICTS = frozenset({"interference"}) | ctl.INVALID_VERDICTS

# Each metric keeps its own units. They are never blended into one number.
METRICS: tuple[tuple[str, str], ...] = (
    ("service_capability", "sessions"),
    ("sustainable_capacity", "clean workflows/s"),
    ("stability_ceiling", "sessions"),
)


def _metric_value(result: dict, name: str):
    if name == "service_capability":
        return (result.get("capability") or {}).get("users")
    if name == "sustainable_capacity":
        return result.get("capacity_workflows_per_s")
    return result.get("stability_ceiling_users", result.get("capacity_users"))


def comparability_key(result: dict) -> dict:
    """The facts that must match for two runs to belong in the same set."""
    repro = result.get("repro") or {}
    host = repro.get("host") or {}
    return {
        "benchmark_target": result.get("benchmark_target"),
        "inference_backend": result.get("inference_backend"),
        "mix": result.get("mix"),
        "load_model": result.get("load_model"),
        "service_class": result.get("service_class"),
        # The weigh-in can land differently across children on a host near a
        # rung boundary; medians across different deadlines mean nothing.
        "service_rung": result.get("service_rung"),
        "scenario_fingerprint": repro.get("scenario_fingerprint"),
        "benchmark_version": repro.get("benchmark_version"),
        "git_commit": repro.get("git_commit"),
        "cpu_model": host.get("cpu_model"),
        "cpu_count": host.get("cpu_count"),
        "mem_total_gb": host.get("mem_total_gb"),
        "numa_nodes": host.get("numa_nodes"),
        "orchestrator_workers": host.get("orchestrator_workers"),
        "database": host.get("database"),
        "prompt_corpus": repro.get("prompt_corpus"),
    }


def aggregate(results: list[dict]) -> dict:
    """Median and observed range per metric, over the accepted runs.

    Median rather than mean: with three samples one contaminated run would
    drag a mean and cannot drag a median."""
    out: dict = {}
    for name, unit in METRICS:
        values = [v for v in (_metric_value(r, name) for r in results)
                  if isinstance(v, (int, float))]
        if not values:
            continue
        median = statistics.median(values)
        out[name] = {
            "unit": unit,
            "n": len(values),
            "median": round(median, 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "values": [round(v, 2) for v in values],
            # How far apart the runs landed, as a share of the median. This is
            # the number that says whether the set agrees with itself.
            "spread_pct": (round(100 * (max(values) - min(values)) / median, 1)
                           if median else None),
        }
    return out


class RepeatSet:
    """Runs one benchmark N times and aggregates. One set at a time."""

    def __init__(self, factory, *, runs: int = DEFAULT_RUNS,
                 seed: int | None = None,
                 settle_s: float = DEFAULT_SETTLE_S,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 quiet_cpu_pct: float = DEFAULT_QUIET_CPU_PCT,
                 quiet_timeout_s: float = DEFAULT_QUIET_TIMEOUT_S):
        self.factory = factory              # (seed: int) -> CapacityTest
        self.runs = max(1, int(runs))
        self.seed = int(seed) if seed is not None else int(time.time()) % 10**6
        self.settle_s = float(settle_s)
        self.max_retries = max(0, int(max_retries))
        self.quiet_cpu_pct = float(quiet_cpu_pct)
        self.quiet_timeout_s = float(quiet_timeout_s)

        self.phase = "idle"
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.current: ctl.CapacityTest | None = None
        self.accepted: list[dict] = []
        self.pinned_rung: str | None = None
        self.excluded: list[dict] = []
        self.result: dict | None = None
        self._stop = asyncio.Event()

    # ── control ──────────────────────────────────────────────────────────────
    def stop(self) -> None:
        self.phase = "stopped"
        self._stop.set()
        if self.current is not None:
            self.current.stop()

    async def run(self) -> None:
        self.phase = "running"
        attempt = 0
        retries_left = self.max_retries
        try:
            while len(self.accepted) < self.runs and not self._stop.is_set():
                if attempt:
                    await self._settle()
                    if self._stop.is_set():
                        break
                seed = self.seed + attempt
                attempt += 1
                logger.info("repeat set: run %d/%d (seed %d)",
                            len(self.accepted) + 1, self.runs, seed)
                # The SET pins the rung: the first accepted child's weigh-in
                # decides, and later children inherit it as a recorded set
                # pin. A host near a rung boundary would otherwise flip rungs
                # on weigh-in noise and shatter every set into disagreeing
                # children. Each child's own weigh-in medians are still
                # measured and recorded — they are the grouping study's data.
                test = self.factory(seed, rung=self.pinned_rung)
                self.current = test
                try:
                    await test.run()
                finally:
                    self.current = None
                res = test.result or {}

                reason = self._exclusion_reason(res, test)
                if reason:
                    self.excluded.append({"seed": seed, "verdict": res.get("verdict"),
                                          "reason": reason,
                                          "history_id": res.get("history_id")})
                    self._dump()
                    if retries_left <= 0:
                        break
                    retries_left -= 1
                    continue

                mismatch = self._comparability_mismatch(res)
                if mismatch:
                    # The benchmark changed under us. Retrying cannot fix that,
                    # and averaging across it would be a lie about what was run.
                    self.excluded.append({"seed": seed, "verdict": res.get("verdict"),
                                          "reason": f"not comparable with run 1: {mismatch}",
                                          "history_id": res.get("history_id")})
                    break

                if self.pinned_rung is None:
                    self.pinned_rung = res.get("service_rung")
                self.accepted.append(res)
                await self._label_child(res, len(self.accepted))
                self._dump()
        finally:
            self._finalize()

    # ── the rules ────────────────────────────────────────────────────────────
    def _exclusion_reason(self, res: dict, test: ctl.CapacityTest) -> str | None:
        if test.phase == "error" or res.get("error"):
            return f"run errored: {res.get('error') or test.error}"
        verdict = res.get("verdict")
        if verdict in ctl.INVALID_VERDICTS:
            return f"{verdict}: the run failed its own integrity check"
        if verdict == "interference":
            return "other tenants saturated the host — the run measured them, not us"
        if res.get("publication_eligible") is False:
            return (res.get("publication_exclusion")
                    or "run is not eligible for a published repeat set")
        if res.get("result_kind") in ("inconclusive", "invalid"):
            return f"no usable number ({res.get('result_kind')})"
        intended = ("sustainable_capacity" if res.get("load_model") == "open"
                    else "service_capability")
        if _metric_value(res, intended) is not None:
            return None
        # A DEFINITIVE non-numeric capability finding is a sample: a level
        # that failed with mature evidence, or a host whose ceiling sits
        # below the conclusive cohort. Three such children agreeing publish
        # as a reproducible finding. Only an indefinite run (stopped, no
        # judgment reached) is not a sample.
        if (intended == "service_capability"
                and (res.get("capability") or {}).get("definitive")):
            return None
        return f"run did not produce its intended metric ({intended})"

    def _comparability_mismatch(self, res: dict) -> str | None:
        if not self.accepted:
            return None
        first, cur = comparability_key(self.accepted[0]), comparability_key(res)
        diffs = [f"{k} {first[k]!r} -> {cur[k]!r}" for k in first if first[k] != cur[k]]
        return "; ".join(diffs) or None

    async def _label_child(self, res: dict, index: int) -> None:
        """Mark the child in benchmark history so set membership is visible
        there. Best-effort: a labelling failure must never fail a good run."""
        rid = res.get("history_id")
        if not rid:
            return
        try:
            from backend.db.base import get_sessionmaker
            from backend.repositories import capacity_runs as caps_repo
            sm = get_sessionmaker()
            async with sm() as session:
                await caps_repo.set_label(
                    session, rid, f"set {self.seed} · run {index}/{self.runs}")
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.debug("could not label child run %s", rid, exc_info=True)

    # ── output ───────────────────────────────────────────────────────────────
    def _finalize(self) -> None:
        self.ended_at = time.time()
        complete = len(self.accepted) >= self.runs
        if self.phase != "stopped":
            self.phase = "done"
        censored = any(r.get("censored") for r in self.accepted)
        intended = None
        if self.accepted:
            intended = ("sustainable_capacity"
                        if self.accepted[0].get("load_model") == "open"
                        else "service_capability")
        metrics = aggregate(self.accepted) if complete else None
        capability_outcome = None
        if complete and intended == "service_capability":
            statuses = [(r.get("capability") or {}).get("status")
                        for r in self.accepted]
            numeric_n = (metrics.get(intended) or {}).get("n") if metrics else 0
            if numeric_n == self.runs:
                pass                             # every child measured a number
            elif len(set(statuses)) == 1 and statuses[0]:
                # Unanimous non-numeric finding: publishable as the finding
                # itself. Mixed outcomes are not a result of any kind.
                capability_outcome = {"finding": statuses[0],
                                      "agreement": f"{self.runs}/{self.runs}",
                                      "highest_failed_users": [
                                          (r.get("capability") or {}).get(
                                              "highest_failed_users")
                                          for r in self.accepted]}
            else:
                complete = False
                metrics = None
        elif complete and intended and (not metrics or
                (metrics.get(intended) or {}).get("n") != self.runs):
            complete = False
            metrics = None
        self.result = {
            "kind": "repeat_set",
            "status": "complete" if complete else "incomplete",
            "runs_requested": self.runs,
            "runs_accepted": len(self.accepted),
            "runs_excluded": len(self.excluded),
            # One censored run bounds the whole set: the median of three floors
            # is itself a floor.
            "censored": censored,
            "censor_reasons": sorted({r.get("censor_reason") for r in self.accepted
                                      if r.get("censor_reason")}),
            # Partial sets carry their child observations but publish no
            # median.  A complete set's intended metric always has n == runs.
            "metrics": metrics,
            "capability_outcome": capability_outcome,
            "intended_metric": intended,
            "excluded": self.excluded,
            "child_run_ids": [r.get("history_id") for r in self.accepted
                              if r.get("history_id")],
            "seeds": [(r.get("repro") or {}).get("seed") for r in self.accepted],
            "pinned_rung": self.pinned_rung,
            "weigh_in_medians": [((r.get("weigh_in") or {}).get("medians_s"))
                                 for r in self.accepted],
            "comparability": comparability_key(self.accepted[0]) if self.accepted else None,
            "base_seed": self.seed,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round(self.ended_at - self.started_at, 1),
        }
        if not complete:
            self.result["incomplete_reason"] = (
                "stopped by hand" if self.phase == "stopped"
                else "children disagreed or retries ran out before enough "
                     "clean runs landed")
        self._dump()

    def _dump(self) -> None:
        """Write the set as it goes. A service restart mid-set then leaves a
        trail of child run ids rather than nothing at all."""
        try:
            ctl.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.started_at))
            payload = self.result or {
                "kind": "repeat_set", "status": "running",
                "runs_requested": self.runs,
                "accepted": [r.get("history_id") for r in self.accepted],
                "excluded": self.excluded, "base_seed": self.seed,
                "started_at": self.started_at,
            }
            (ctl.RESULTS_DIR / f"repeat-{stamp}.json").write_text(
                json.dumps(payload, indent=1))
        except OSError:
            pass

    # ── settling between runs ────────────────────────────────────────────────
    async def _settle(self) -> None:
        """Idle, then wait for the box to go quiet before the next run.

        Back-to-back runs would measure each other's tail: page cache, dying
        connections, and the previous run's stragglers all land in the next
        run's warm-up."""
        self.phase = "settling"
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.settle_s)
            return
        except asyncio.TimeoutError:
            pass
        sampler = SystemSampler()
        sampler.sample()                       # first sample primes the delta
        deadline = time.time() + self.quiet_timeout_s
        while time.time() < deadline and not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
                return
            except asyncio.TimeoutError:
                pass
            cpu = (sampler.sample() or {}).get("cpu_pct")
            if cpu is None or cpu <= self.quiet_cpu_pct:
                break
        self.phase = "running"

    # ── live status ──────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {
            "active": self.phase in ("running", "settling"),
            "phase": self.phase,
            "runs_requested": self.runs,
            "runs_accepted": len(self.accepted),
            "runs_excluded": len(self.excluded),
            "excluded": self.excluded,
            "base_seed": self.seed,
            "elapsed_s": round(time.time() - self.started_at, 1),
            "current": self.current.status() if self.current else None,
            "result": self.result,
        }
