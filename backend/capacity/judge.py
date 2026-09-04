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
import collections
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


SWEEP_VERSION = "sweep-2"


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q / 100.0 * (len(xs) - 1)))))
    return xs[k]


def sweep(path: str | Path, window_s: float = 30.0,
          think_s: float = 3.0, target: float = 0.95,
          confidence: float = 0.95) -> dict:
    """Rate-sweep post-processing: the latency-versus-load curve, and the
    sustainable rate and derived session capacity for EVERY service tier,
    from one ledger.

    Arrivals are bucketed into fixed windows. A window qualifies for a tier
    when every workflow type's on-time fraction against that tier's deadline
    clears the joint Wilson bound AND completions keep up with arrivals
    (no backlog growth). A tier's sustainable rate is the highest window
    rate confirmed by a second qualifying window within 10%. Sessions
    follow as rate x (median latency at that rate + think time). Works on
    open-loop ledgers directly; on closed-loop ledgers the windows are the
    observed operating points rather than a controlled sweep."""
    from backend.capacity.scenarios import service_ladder
    ev = read_evidence(path)
    units = sorted((float(u["sub"]), float(u["end"]) if u.get("end") else None,
                    u.get("sid") or "?", bool(u.get("ok")),
                    float(u["lat"]) if u.get("lat") is not None else None)
                   for u in ev["units"] if u.get("sub"))
    if not units:
        return {"sweep_version": SWEEP_VERSION, "windows": [], "tiers": {}}
    sids = sorted({u[2] for u in units})
    z = st.familywise_z(max(1, len(sids)), confidence)
    ladder = service_ladder()
    t0 = units[0][0]
    t1 = max(u[0] for u in units)
    subs = [u[0] for u in units]
    ends = sorted(u[1] for u in units if u[1] is not None)

    windows = []
    t = t0
    while t < t1:
        hi = t + window_s
        win = [u for u in units if t <= u[0] < hi]
        if len(win) >= 3 * len(sids):
            arrival_rate = len(win) / window_s
            completions = bisect.bisect_left(ends, hi) - bisect.bisect_left(ends, t)
            lats = [u[4] for u in win if u[3] and u[4] is not None]
            # Steady state is judged by BACKLOG GROWTH, not same-window
            # completion matching: with long workflows, completions in a
            # window answer the PREVIOUS window's arrivals, so a climbing
            # schedule always shows a same-window deficit equal to one
            # latency of growth (sweep-1 failed every window of a healthy
            # run this way). Backlog delta - arrivals to date minus
            # completions to date, changed across this window - measures
            # keeping-up directly, at any latency.
            arrived_before = bisect.bisect_left(subs, t)
            arrived_by_end = bisect.bisect_left(subs, hi)
            done_before = bisect.bisect_left(ends, t)
            done_by_end = bisect.bisect_left(ends, hi)
            backlog_delta = ((arrived_by_end - done_by_end)
                             - (arrived_before - done_before))
            row = {"t": round(t - t0, 1),
                   "rate": round(arrival_rate, 2),
                   "completion_rate": round(completions / window_s, 2),
                   "backlog_delta": backlog_delta,
                   "p50_ms": _pct(lats, 50), "p95_ms": _pct(lats, 95),
                   "tiers_ok": {}}
            keeps_up = backlog_delta <= max(5, int(0.05 * len(win)))
            for tier, dl in ladder.items():
                if dl is None:
                    continue
                bounds = []
                for sid in sids:
                    xs = [u for u in win if u[2] == sid]
                    wins_ = sum(1 for u in xs
                                if u[3] and u[4] is not None
                                and u[4] <= float(dl) * 1000.0)
                    bounds.append(st.wilson_lower(wins_, len(xs), z)
                                  if xs else 0.0)
                row["tiers_ok"][tier] = bool(bounds and min(bounds) >= target
                                             and keeps_up)
            windows.append(row)
        t = hi

    tiers_out = {}
    for tier, dl in ladder.items():
        if dl is None:
            continue
        ok_rows = [w for w in windows if w["tiers_ok"].get(tier)]
        best = None
        for w in sorted(ok_rows, key=lambda w: -w["rate"]):
            near = [v for v in ok_rows
                    if v is not w and abs(v["rate"] - w["rate"]) <= 0.1 * w["rate"]]
            if near:
                best = w
                break
        if best is None:
            tiers_out[tier] = {"sustainable_rate": None, "confirmed": False}
            continue
        band = [v for v in ok_rows
                if abs(v["rate"] - best["rate"]) <= 0.1 * best["rate"]]
        med_lat = _pct([v["p50_ms"] for v in band if v["p50_ms"]], 50) or 0.0
        tiers_out[tier] = {
            "sustainable_rate": best["rate"],
            "confirmed": True,
            "p95_ms_at_rate": best["p95_ms"],
            "deadline_s": float(dl),
            "derived_sessions": int(best["rate"]
                                    * (med_lat / 1000.0 + think_s)),
        }

    return {"sweep_version": SWEEP_VERSION, "window_s": window_s,
            "think_s": think_s, "units": len(units),
            "windows": windows, "tiers": tiers_out}


PLATEAU_VERSION = "plateau-1"


def plateau(paths: list, think_s: float = 3.0, target: float = 0.95,
            confidence: float = 0.95, warmup_x: float = 1.5) -> dict:
    """Plateau post-processing: judge ONE held rate from one or more ledgers
    (the instances of a fleet run at the same rate, pooled).

    Why not sweep-2's 30-second windows: at 2 workflows/s an instance
    admits 60 units per window, 20 per type, and the joint Wilson bound on
    20 of 20 is 0.83 - a perfectly on-time plateau is unjudgeable window
    by window. A plateau is one operating point, so it is judged as one
    cohort: every unit admitted after warm-up (warmup_x times the slowest
    completed latency, so the queue has filled to its steady depth) and
    before the last arrival. Per-type on-time fractions against each tier
    deadline take the joint Wilson bound over that whole cohort; steady
    state is backlog growth across the cohort span (arrivals-to-date minus
    completions-to-date at the end of the span versus its start), within
    max(5, 5% of cohort arrivals). Sessions resident = rate x (median
    latency + think), Little's law."""
    from backend.capacity.scenarios import service_ladder
    units = []
    per_ledger = []
    offered = []
    ended_at = None
    for path in paths:
        ev = read_evidence(path)
        rs = sorted(float(u["r"]) for u in ev["units"] if u.get("r") is not None)
        offered.append(rs[len(rs) // 2] if rs else None)
        ft = ev.get("footer") or {}
        if ft.get("ended_at"):
            ended_at = max(ended_at or 0.0, float(ft["ended_at"]))
        # Weigh-in units (admitted before the generator starts, no offered
        # rate on the row) are not the plateau's arrivals; counting them
        # stretched the span and read as a 4-5% generator shortfall on
        # every 300 s plateau of a fresh fingerprint (series 7702).
        has_rate = any(u.get("r") is not None for u in ev["units"])
        us = [(float(u["sub"]), float(u["end"]) if u.get("end") else None,
               u.get("sid") or "?", bool(u.get("ok")),
               float(u["lat"]) if u.get("lat") is not None else None,
               (u.get("err") or "")[:40])
              for u in ev["units"]
              if u.get("sub") and (not has_rate or u.get("r") is not None)]
        units.extend(us)
        per_ledger.append(len(us))
    if not units:
        return {"plateau_version": PLATEAU_VERSION, "units": 0}
    units.sort()
    sids = sorted({u[2] for u in units})
    z = st.familywise_z(max(1, len(sids)), confidence)
    ladder = service_ladder()
    t0, t_last = units[0][0], units[-1][0]
    slowest = max((u[4] for u in units if u[3] and u[4]), default=0.0) / 1000.0
    start = t0 + min(warmup_x * slowest, 0.5 * (t_last - t0))
    cohort = [u for u in units if start <= u[0] <= t_last]
    span = max(1e-9, t_last - start)
    rate = len(cohort) / span
    subs = [u[0] for u in units]
    ends = sorted(u[1] for u in units if u[1] is not None)
    backlog_start = bisect.bisect_left(subs, start) - bisect.bisect_left(ends, start)
    backlog_end = bisect.bisect_right(subs, t_last) - bisect.bisect_left(ends, t_last)
    backlog_delta = backlog_end - backlog_start
    keeps_up = backlog_delta <= max(5, int(0.05 * len(cohort)))
    offered_sum = sum(o for o in offered if o) if offered else 0.0
    achieved_ratio = (rate / offered_sum) if offered_sum else None
    generator_ok = achieved_ratio is None or achieved_ratio >= 0.95
    # Units in flight when the run ended are censored: their age at the
    # end decides. Younger than a tier's deadline = PENDING for that tier
    # (neither success nor failure, out of the denominator); older = late.
    run_end = ended_at if ended_at is not None else max(
        [u[1] for u in units if u[1] is not None] + [t_last])
    def _age_ms(u):
        return (run_end - u[0]) * 1000.0
    per_type = {}
    for sid in sids:
        xs = [u for u in cohort if u[2] == sid]
        lats = sorted(u[4] for u in xs if u[3] and u[4] is not None)
        failed = collections.Counter(u[5] for u in xs if not u[3])
        per_type[sid] = {"n": len(xs), "ok": len(lats),
                         "p50_ms": _pct(lats, 50), "p95_ms": _pct(lats, 95),
                         "failed": dict(failed)}
    tiers = {}
    for tier, dl in ladder.items():
        if dl is None:
            continue
        bounds = {}
        for sid in sids:
            xs = [u for u in cohort if u[2] == sid
                  and not (u[5] == "inflight_at_end"
                           and _age_ms(u) < float(dl) * 1000.0)]
            wins = sum(1 for u in xs if u[3] and u[4] is not None
                       and u[4] <= float(dl) * 1000.0)
            bounds[sid] = round(st.wilson_lower(wins, len(xs), z), 4) if xs else 0.0
        ok = (bool(bounds) and min(bounds.values()) >= target and keeps_up
              and generator_ok)
        tiers[tier] = {"deadline_s": float(dl), "bounds": bounds,
                       "on_time_and_steady": ok}
    all_lats = sorted(u[4] for u in cohort if u[3] and u[4] is not None)
    med = (_pct(all_lats, 50) or 0.0) / 1000.0
    # The generator's receipt: the cohort's achieved arrival rate against
    # the rate the ledgers say was offered (unit rows carry it). A plateau
    # whose generator fell behind describes load nobody offered, and its
    # backlog then shrinks for the wrong reason - such a plateau is
    # censored (generator_limit), never certified.
    pending = sum(1 for u in cohort if u[5] == "inflight_at_end")
    return {"plateau_version": PLATEAU_VERSION, "ledgers": len(paths),
            "inflight_at_end": pending,
            "offered_rate": round(offered_sum, 3) if offered_sum else None,
            "achieved_ratio": (round(achieved_ratio, 3)
                               if achieved_ratio is not None else None),
            "generator_ok": generator_ok,
            "units": len(units), "units_per_ledger": per_ledger,
            "cohort_units": len(cohort), "warmup_s": round(start - t0, 1),
            "span_s": round(span, 1), "rate": round(rate, 3),
            "backlog_start": backlog_start, "backlog_end": backlog_end,
            "backlog_delta": backlog_delta, "keeps_up": keeps_up,
            "per_type": per_type, "tiers": tiers,
            "sustained_tiers": [t for t, v in tiers.items()
                                if v["on_time_and_steady"]],
            "resident_sessions": int(rate * (med + think_s)),
            "think_s": think_s}


def summarize(judgment: dict) -> dict:
    """The compact slice that rides inside a run result."""
    return {k: judgment[k] for k in
            ("judge_version", "capability_users", "first_failing_level",
             "levels_judged", "units_judged", "deadline_s", "notes")}


STAGE_GROUPS = {
    "model_wait": ("model_wait_ms",),
    "retrieval": ("retrieve_ms",),
    "rerank_call": ("rerank_call_ms",),
    "rerank_backoff": ("rerank_429_backoff_ms",),
    "sandbox_wall": ("sandbox_light_wall_ms", "sandbox_heavy_wall_ms", "sandbox_xl_wall_ms",
                     "sandbox_build_wall_ms", "sandbox_ingest_wall_ms"),
    "sandbox_cpu": ("sandbox_light_cpu_ms", "sandbox_heavy_cpu_ms", "sandbox_xl_cpu_ms",
                    "sandbox_build_cpu_ms", "sandbox_ingest_cpu_ms"),
    "ingest_embed": ("ingest_embed_ms",),
    "ingest_index": ("ingest_index_ms",),
}


def stages(paths: list, warmup_x: float = 1.5) -> dict:
    """Per-archetype stage breakdown of one held rate, from the per-unit
    stage sums the executors put on each ledger row (`st`).

    For every archetype in the plateau's cohort (same warm-up rule as
    `plateau`): units, median latency, and the median per-unit SUM of each
    stage group in seconds - model wait as modeled by the stand-in, the
    retrieval pipeline, the reranker call inside it, backoff after a
    refusal, sandboxed job wall and CPU. The sums are resource time, not
    the critical path (parallel workers add), so the reading is across
    rates: the stage whose per-unit sum inflates as the rate rises is
    where that archetype's slowdown lives."""
    units = []
    for path in paths:
        ev = read_evidence(path)
        has_rate = any(u.get("r") is not None for u in ev["units"])
        for u in ev["units"]:
            if not u.get("sub") or (has_rate and u.get("r") is None):
                continue
            units.append((float(u["sub"]), u.get("sid") or "?", bool(u.get("ok")),
                          float(u["lat"]) if u.get("lat") is not None else None,
                          u.get("st") or {}))
    if not units:
        return {"stages_version": "stages-1", "units": 0, "per_type": {}}
    units.sort(key=lambda u: u[0])
    t0, t_last = units[0][0], units[-1][0]
    slowest = max((u[3] for u in units if u[2] and u[3]), default=0.0) / 1000.0
    start = t0 + min(warmup_x * slowest, 0.5 * (t_last - t0))
    cohort = [u for u in units if start <= u[0] <= t_last and u[2] and u[3] is not None]
    per: dict[str, dict] = {}
    for sid in sorted({u[1] for u in cohort}):
        us = [u for u in cohort if u[1] == sid]
        with_st = [u for u in us if u[4]]
        row = {"n": len(us), "with_stages": len(with_st),
               "p50_s": round(_pct([u[3] for u in us], 50) / 1000.0, 1),
               "p95_s": round(_pct([u[3] for u in us], 95) / 1000.0, 1)}
        for g, keys in STAGE_GROUPS.items():
            sums = [sum(float((u[4].get(k) or [0, 0])[0]) for k in keys) for u in with_st]
            cnts = [sum(int((u[4].get(k) or [0, 0])[1]) for k in keys) for u in with_st]
            if with_st and any(cnts):
                row[g] = {"p50_s": round(_pct(sums, 50) / 1000.0, 2),
                          "p95_s": round(_pct(sums, 95) / 1000.0, 2),
                          "calls": round(sum(cnts) / len(cnts), 2)}
        per[sid] = row
    return {"stages_version": "stages-1", "units": len(cohort), "per_type": per}


def stages_table(paths: list) -> str:
    """The breakdown as a markdown table (seconds per unit, p50 of sums)."""
    s = stages(paths)
    cols = list(STAGE_GROUPS)
    lines = ["| archetype | n | latency p50/p95 | " + " | ".join(cols) + " |",
             "|---|---|---|" + "---|" * len(cols)]
    for sid, r in s["per_type"].items():
        cells = []
        for g in cols:
            v = r.get(g)
            cells.append(f"{v['p50_s']} ({v['calls']:g} calls)" if v else "-")
        lines.append(f"| {sid} | {r['n']} | {r['p50_s']}/{r['p95_s']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:  # pragma: no cover — thin CLI
    import argparse
    ap = argparse.ArgumentParser(
        description="Re-judge a capacity evidence ledger.")
    ap.add_argument("evidence", help="path to evidence-*.jsonl.gz")
    ap.add_argument("-o", "--out", help="write full judgment JSON here")
    ap.add_argument("--sweep", action="store_true",
                    help="rate-sweep post-processing instead of the "
                         "capability judgment")
    ap.add_argument("--plateau", nargs="*", metavar="LEDGER",
                    help="judge one held rate from these ledgers (pooled) "
                         "plus the positional one")
    ap.add_argument("--stages", nargs="*", metavar="LEDGER",
                    help="per-archetype stage breakdown of one held rate "
                         "from these ledgers plus the positional one")
    args = ap.parse_args()
    if args.stages is not None:
        print(stages_table([args.evidence, *args.stages]))
        return
    if args.plateau is not None:
        p = plateau([args.evidence, *args.plateau])
        if args.out:
            Path(args.out).write_text(json.dumps(p, indent=1))
        print(json.dumps(p, indent=1))
        return
    if args.sweep:
        s = sweep(args.evidence)
        if args.out:
            Path(args.out).write_text(json.dumps(s, indent=1))
        print(json.dumps({k: s[k] for k in
                          ("sweep_version", "units", "tiers")}, indent=1))
        return
    j = judge_evidence(args.evidence)
    if args.out:
        Path(args.out).write_text(json.dumps(j, indent=1))
    print(json.dumps({**summarize(j),
                      "levels": j["levels"][-8:]}, indent=1))


if __name__ == "__main__":
    main()
