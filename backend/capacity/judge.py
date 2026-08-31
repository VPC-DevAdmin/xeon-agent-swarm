"""
Offline judge: verdicts as a versioned pure function over an evidence ledger.

The in-run judge steers the ramp and its verdicts are provisional. This
module re-derives the service-capability verdict from the ledger alone, so
judgment rules can evolve and be re-applied to history without re-running
any load. Each judgment records its judge_version; two judgments of the
same ledger under the same version are identical.

post-1 rules (why they exist):
  * A unit succeeds only if it completed AND finished inside the rung
    deadline. The in-run judge once let a level with p95 ten times the
    deadline stay "good" because stability drift was judged window-over-
    window with no absolute anchor (seed 20690 certified 3,756 sessions
    while p95 sat at 160 s against a 15 s deadline).
  * Timed-out and errored units are DECIDED FAILURES at the level that
    admitted them, never pending. The same run culled its backlog with
    timeouts at the final hold, and the in-run window then only saw the
    fresh survivors.
  * A unit belongs to the concurrency level that was active when it was
    SUBMITTED. Work admitted at 3,750 users is evidence about 3,750
    users, whenever it happens to finish.
  * Certification is blocked upward by the first failing level: the
    capability figure is the highest passing level below it. Levels
    with too little evidence neither pass nor block.

post-2 (supersedes post-1's failure rule):
  * A level FAILS only when its Wilson UPPER bound sits below the
    target - the evidence must refute passing, not merely fail to
    prove it. post-1 treated one failure in a thin level as a
    confident failure: 13 spurious DB-read hiccups across a fleet run
    turned isolated single failures at thin ramp levels into blocking
    walls and crushed a healthy 654-session instance to 258. Passing
    still requires the lower bound to clear the target; between the
    two bounds is silence.
"""
from __future__ import annotations

import bisect
import json
import logging
from pathlib import Path

from backend.capacity import stats as st
from backend.capacity.evidence import read_evidence

logger = logging.getLogger(__name__)

JUDGE_VERSION = "post-2"


def _level_at(times: list[float], values: list[int], t: float | None) -> int:
    """The concurrency active at time t, from the sample series."""
    if t is None or not times:
        return 0
    i = bisect.bisect_right(times, float(t)) - 1
    return values[i] if i >= 0 else 0


def judge_evidence(path: str | Path) -> dict:
    ev = read_evidence(path)
    header = ev.get("header") or {}
    footer = ev.get("footer") or {}
    # The rung (and so the deadline) is decided by the weigh-in after the
    # header is written, so it travels in the footer.
    deadline_s = header.get("deadline_s")
    if deadline_s is None:
        deadline_s = footer.get("deadline_s")
    target = float(header.get("capability_target", 0.95))
    confidence = float(header.get("capability_confidence", 0.95))
    sids = sorted({u.get("sid") for u in ev["units"] if u.get("sid")})
    notes: list[str] = []
    if deadline_s is None:
        notes.append("no rung deadline in header: completion is the only "
                     "success criterion, so this judgment certifies "
                     "completion, not service")
    z = st.familywise_z(max(1, len(sids)), confidence)

    samples = sorted((float(s["ts"]), int(s.get("users") or 0))
                     for s in ev["samples"] if s.get("ts") is not None)
    times = [t for t, _ in samples]
    values = [u for _, u in samples]

    # Units, grouped by the level that admitted them.
    by_level: dict[int, dict[str, list[int]]] = {}
    for u in ev["units"]:
        lvl = _level_at(times, values, u.get("sub"))
        if lvl <= 0:
            continue
        on_time = bool(u.get("ok")) and (
            deadline_s is None
            or (u.get("lat") is not None
                and float(u["lat"]) <= float(deadline_s) * 1000.0))
        per = by_level.setdefault(lvl, {})
        per.setdefault(u.get("sid") or "?", []).append(1 if on_time else 0)

    levels_out = []
    first_failing = None
    for lvl in sorted(by_level):
        per = by_level[lvl]
        per_type = {}
        bounds = []
        uppers = []
        for sid in sids:
            xs = per.get(sid, [])
            n, wins = len(xs), sum(xs)
            bound = round(st.wilson_lower(wins, n, z), 4) if n else 0.0
            upper = round(st.wilson_upper(wins, n, z), 4) if n else 1.0
            per_type[sid] = {"decided": n, "on_time": wins, "bound": bound,
                             "upper": upper}
            bounds.append(bound)
            uppers.append(upper)
        passing = bool(bounds) and min(bounds) >= target
        # Refutation, not absence of proof: some type's on-time rate is
        # confidently below the target even under the optimistic bound.
        failing = bool(uppers) and min(uppers) < target
        levels_out.append({"users": lvl, "per_type": per_type,
                           "joint_bound": min(bounds) if bounds else 0.0,
                           "pass": passing,
                           "fail": failing})
        if failing and first_failing is None:
            first_failing = lvl

    certified = None
    for row in levels_out:
        if first_failing is not None and row["users"] >= first_failing:
            break
        if row["pass"]:
            certified = row["users"]

    return {
        "judge_version": JUDGE_VERSION,
        "deadline_s": deadline_s,
        "confidence": confidence,
        "target": target,
        "capability_users": certified,
        "first_failing_level": first_failing,
        "levels_judged": len(levels_out),
        "units_judged": sum(len(v) for per in by_level.values()
                            for v in per.values()),
        "levels": levels_out,
        "notes": notes,
    }


def summarize(judgment: dict) -> dict:
    """The compact slice that rides inside a run result."""
    return {k: judgment[k] for k in
            ("judge_version", "capability_users", "first_failing_level",
             "levels_judged", "units_judged", "deadline_s", "notes")}


def main() -> None:  # pragma: no cover — thin CLI
    import argparse
    ap = argparse.ArgumentParser(
        description="Re-judge a capacity evidence ledger.")
    ap.add_argument("evidence", help="path to evidence-*.jsonl.gz")
    ap.add_argument("-o", "--out", help="write full judgment JSON here")
    args = ap.parse_args()
    j = judge_evidence(args.evidence)
    if args.out:
        Path(args.out).write_text(json.dumps(j, indent=1))
    print(json.dumps({**summarize(j),
                      "levels": j["levels"][-8:]}, indent=1))


if __name__ == "__main__":
    main()
