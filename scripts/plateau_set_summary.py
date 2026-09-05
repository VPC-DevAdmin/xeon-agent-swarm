"""Summarize a certified plateau set: plateau-1 judgments per rate per series,
medians across series, and the set's headline.

    PYTHONPATH=. .venv/bin/python scripts/plateau_set_summary.py data/capacity/set-<stamp>

Writes <set>/summary.json and <set>/summary.md. The headline is the
highest rate at which every series sustains the same tier (joint 95/95
on-time, backlog steady, generator within 5% of the offered rate), reported
per tier, with the median and range of the fleet-wide achieved rate and of
the Little's-law residency across the three series.
"""
from __future__ import annotations

import collections
import glob
import json
import re
import statistics as st
import sys
from pathlib import Path

from backend.capacity.evidence import read_evidence
from backend.capacity.judge import plateau



def _rate_of(path: str) -> float:
    """Per-instance rate from a series file name; integers stay integers
    (rate-8-i1 -> 8), fractional rates are floats (rate-0.5-i1 -> 0.5)."""
    import re as _re
    r = _re.search(r"rate-([0-9.]+)-i", path).group(1)
    return int(r) if r.isdigit() else float(r)

def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def judge_series(series_dir: str) -> dict:
    out = {}
    rates = sorted({_rate_of(f)
                    for f in glob.glob(f"{series_dir}/rate-*-i*-evidence-*.jsonl.gz")})
    for r in rates:
        files = sorted(glob.glob(f"{series_dir}/rate-{r}-i*-evidence-*.jsonl.gz"))
        pj = plateau(files)
        cpu, ret, trunc = [], [], 0
        for f in files:
            ev = read_evidence(f)
            trunc += 1 if ev.get("truncated") else 0
            smp = [s for s in ev["samples"] if s.get("cpu_pct") is not None]
            mid = smp[len(smp) // 4: 3 * len(smp) // 4] or smp
            if mid:
                cpu.append(pct([s["cpu_pct"] for s in mid], .5))
                ret.append(pct([s["cpu_by"].get("retrieval", 0) for s in mid if s.get("cpu_by")], .5) or 0)
        pj["host_cpu_pct"] = round(st.median(cpu), 1) if cpu else None
        pj["retrieval_cpu_pct"] = round(st.median(ret), 1) if ret else None
        pj["truncated_ledgers"] = trunc
        pj["per_instance_rate"] = r
        out[r] = pj
    return out


def main() -> None:
    set_dir = Path(sys.argv[1])
    dirs = [d.strip() for d in (set_dir / "series-dirs.txt").read_text().splitlines() if d.strip()]
    series = {d: judge_series(d) for d in dirs}
    rates = sorted({r for s in series.values() for r in s})
    tiers = ["conversational", "interactive", "responsive", "attended", "queued", "background"]
    summary = {"set": str(set_dir), "series": dirs, "rates": rates, "per_rate": {}, "headline": {}}
    # Per-type columns follow the archetypes present in the set (the
    # reference tile and the CPU-heavy tile differ).
    sids = sorted({sid for s in series.values() for j in s.values() for sid in (j.get("per_type") or {})})
    short = {sid: sid.replace("_", " ")[:14] for sid in sids}
    lines = [f"# Plateau set {set_dir.name}", "", f"Series: {', '.join(Path(d).name for d in dirs)}", "",
             "| rate/inst | achieved (median, range) | gen ok | keeps up | "
             + " | ".join(f"{short[sid]} p50/p95" for sid in sids)
             + " | tiers sustained (series pooled) | resident (median) | host CPU | retrieval CPU |",
             "|---|---|---|---|" + "---|" * len(sids) + "---|---|---|---|"]
    for r in rates:
        js = [series[d][r] for d in dirs if r in series[d]]
        ach = [j["rate"] for j in js]
        # A tier is sustained by the SET when every series keeps up and the
        # joint 95/95 bound holds over the three series' cohorts POOLED
        # (the same rule plateau-1 applies within a series). At low rates a
        # single ten-minute hold carries too few units per type to clear
        # 0.95 jointly even with every unit on time (64 of 64 bounds at
        # 0.91 with five types); pooling the seeds is the set's evidence,
        # and it is post-processing of the same ledgers.
        pooled = plateau([f for d in dirs if r in series[d]
                          for f in sorted(glob.glob(f"{d}/rate-{r}-i*-evidence-*.jsonl.gz"))])
        all_keep = all(j["keeps_up"] and j["generator_ok"] for j in js)
        # The pooled cohort spans three separate holds, so its own backlog
        # arithmetic is meaningless; steadiness is the per-series verdict
        # and the pooled plateau contributes only the joint on-time bounds.
        target = 0.95
        sustained = [t for t in tiers if all_keep and t in (pooled.get("tiers") or {})
                     and all(v >= target for v in pooled["tiers"][t]["bounds"].values())]
        per_series_sustained = [t for t in tiers if all(t in j["sustained_tiers"] for j in js)]

        def lat(sid, q):
            xs = [j["per_type"].get(sid, {}).get(q) for j in js]
            xs = [x for x in xs if x is not None]
            return f"{st.median(xs) / 1000:.0f}" if xs else "-"
        row = {"achieved_median": round(st.median(ach), 2), "achieved_range": [round(min(ach), 2), round(max(ach), 2)],
               "generator_ok_all": all(j["generator_ok"] for j in js),
               "keeps_up_all": all(j["keeps_up"] for j in js),
               "tiers_sustained_all_series": sustained,
               "tiers_sustained_per_series": per_series_sustained,
               "pooled_tiers": pooled.get("tiers"),
               "resident_median": int(st.median([j["resident_sessions"] for j in js])),
               "resident_range": [min(j["resident_sessions"] for j in js), max(j["resident_sessions"] for j in js)],
               "host_cpu_median": st.median([j["host_cpu_pct"] for j in js if j["host_cpu_pct"] is not None] or [0]),
               "retrieval_cpu_median": st.median([j["retrieval_cpu_pct"] for j in js if j["retrieval_cpu_pct"] is not None] or [0]),
               "per_series": {Path(d).name: series[d][r] for d in dirs if r in series[d]}}
        summary["per_rate"][r] = row
        lines.append(f"| {r} | {row['achieved_median']} ({row['achieved_range'][0]}-{row['achieved_range'][1]}) | "
                     f"{'yes' if row['generator_ok_all'] else 'NO'} | {'yes' if row['keeps_up_all'] else 'NO'} | "
                     + " | ".join(f"{lat(sid, 'p50_ms')}/{lat(sid, 'p95_ms')}" for sid in sids) + " | "
                     f"{', '.join(sustained) or 'none'} | {row['resident_median']} ({row['resident_range'][0]}-{row['resident_range'][1]}) | "
                     f"{row['host_cpu_median']}% | {row['retrieval_cpu_median']}% |")
    for t in tiers:
        ok = [r for r in rates if t in summary["per_rate"][r]["tiers_sustained_all_series"]]
        if ok:
            r = max(ok)
            summary["headline"][t] = {"per_instance_rate": r,
                                      "fleet_rate_median": summary["per_rate"][r]["achieved_median"],
                                      "fleet_rate_range": summary["per_rate"][r]["achieved_range"],
                                      "resident_median": summary["per_rate"][r]["resident_median"],
                                      "resident_range": summary["per_rate"][r]["resident_range"]}
    lines += ["", "## Headline (highest rate every series keeps up at and the pooled cohort sustains, per tier)", ""]
    for t, h in summary["headline"].items():
        lines.append(f"- **{t}**: {h['fleet_rate_median']} workflows/s box-wide (range {h['fleet_rate_range'][0]}-{h['fleet_rate_range'][1]}), "
                     f"{h['resident_median']} resident (range {h['resident_range'][0]}-{h['resident_range'][1]})")
    (set_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (set_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
