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


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def judge_series(series_dir: str) -> dict:
    out = {}
    rates = sorted({int(re.search(r"rate-(\d+)-i", f).group(1))
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
    lines = [f"# Plateau set {set_dir.name}", "", f"Series: {', '.join(Path(d).name for d in dirs)}", "",
             "| rate/inst | achieved (median, range) | gen ok | keeps up | researcher p50/p95 | comparison p50/p95 | digest p50/p95 | tiers sustained (all series) | resident (median) | host CPU | retrieval CPU |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rates:
        js = [series[d][r] for d in dirs if r in series[d]]
        ach = [j["rate"] for j in js]
        sustained = [t for t in tiers if all(t in j["sustained_tiers"] for j in js)]

        def lat(sid, q):
            xs = [j["per_type"].get(sid, {}).get(q) for j in js]
            xs = [x for x in xs if x is not None]
            return f"{st.median(xs) / 1000:.0f}" if xs else "-"
        row = {"achieved_median": round(st.median(ach), 2), "achieved_range": [round(min(ach), 2), round(max(ach), 2)],
               "generator_ok_all": all(j["generator_ok"] for j in js),
               "keeps_up_all": all(j["keeps_up"] for j in js),
               "tiers_sustained_all_series": sustained,
               "resident_median": int(st.median([j["resident_sessions"] for j in js])),
               "resident_range": [min(j["resident_sessions"] for j in js), max(j["resident_sessions"] for j in js)],
               "host_cpu_median": st.median([j["host_cpu_pct"] for j in js if j["host_cpu_pct"] is not None] or [0]),
               "retrieval_cpu_median": st.median([j["retrieval_cpu_pct"] for j in js if j["retrieval_cpu_pct"] is not None] or [0]),
               "per_series": {Path(d).name: series[d][r] for d in dirs if r in series[d]}}
        summary["per_rate"][r] = row
        lines.append(f"| {r} | {row['achieved_median']} ({row['achieved_range'][0]}-{row['achieved_range'][1]}) | "
                     f"{'yes' if row['generator_ok_all'] else 'NO'} | {'yes' if row['keeps_up_all'] else 'NO'} | "
                     f"{lat('research_brief', 'p50_ms')}/{lat('research_brief', 'p95_ms')} | "
                     f"{lat('comparison', 'p50_ms')}/{lat('comparison', 'p95_ms')} | {lat('digest', 'p50_ms')}/{lat('digest', 'p95_ms')} | "
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
    lines += ["", "## Headline (highest rate every series sustains, per tier)", ""]
    for t, h in summary["headline"].items():
        lines.append(f"- **{t}**: {h['fleet_rate_median']} workflows/s box-wide (range {h['fleet_rate_range'][0]}-{h['fleet_rate_range'][1]}), "
                     f"{h['resident_median']} resident (range {h['resident_range'][0]}-{h['resident_range'][1]})")
    (set_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (set_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
