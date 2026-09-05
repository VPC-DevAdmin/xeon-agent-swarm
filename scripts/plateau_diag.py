"""Read one plateau series directory stage by stage.

    PYTHONPATH=. .venv/bin/python scripts/plateau_diag.py data/capacity/series-7104-... \
        [--probe data/capacity/series-7104-probe.log] [--stats data/capacity/retrieval]

Prints, per plateau: per-instance latency by type, failures, host CPU and
attribution, the plateau judgment (rule plateau-1), a per-30 s time series
of arrivals / research p95 / in-flight, and, when available, the tier
probe (latency seen from outside the executors) and the executors'
retrieval stage timings (gate wait vs call, per 30 s flush).
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("--probe")
    ap.add_argument("--stats")
    ap.add_argument("--no-series", action="store_true")
    a = ap.parse_args()
    d = a.series_dir
    rates = sorted({_rate_of(f)
                    for f in glob.glob(f"{d}/rate-*-i*-evidence-*.jsonl.gz")})
    for r in rates:
        print(f"=== plateau {r}/s per instance")
        files = sorted(glob.glob(f"{d}/rate-{r}-i*-evidence-*.jsonl.gz"))
        try:
            from backend.capacity.judge import stages_table
            print(stages_table(files))
        except Exception as exc:  # noqa: BLE001
            print("  (stage breakdown unavailable:", exc, ")")
        lat = collections.defaultdict(list)
        for f in files:
            inst = re.search(r"-(i\d)-", f).group(1)
            ev = read_evidence(f)
            by = collections.defaultdict(list)
            bad = collections.Counter()
            for u in ev["units"]:
                if u.get("ok") and u.get("lat") is not None:
                    by[u["sid"]].append(u["lat"] / 1000)
                    lat[u["sid"]].append(u["lat"] / 1000)
                else:
                    bad[(u.get("sid") or "?")[:6] + ":" + (u.get("err") or "")[:30]] += 1
            smp = [s for s in ev["samples"] if s.get("cpu_pct") is not None]
            mid = smp[len(smp) // 4: 3 * len(smp) // 4] or smp
            cpu = pct([s["cpu_pct"] for s in mid], 0.5)
            groups = collections.defaultdict(list)
            for s in mid:
                for k, v in (s.get("cpu_by") or {}).items():
                    groups[k].append(v)
            g = {k: round(pct(v, 0.5), 1) for k, v in groups.items()}
            ex = [s["cpu_exec"] for s in mid if s.get("cpu_exec")]
            exec_spread = ""
            if ex:
                exec_spread = (f" exec%thread min/p50/max={pct([e['min'] for e in ex], .5):.0f}/"
                               f"{pct([e['p50'] for e in ex], .5):.0f}/{pct([e['max'] for e in ex], .5):.0f}")
            subs = sorted(u["sub"] for u in ev["units"] if u.get("sub"))
            span = (subs[-1] - subs[0]) if len(subs) > 1 else 0
            gen = ""
            if smp and "gen_shed" in smp[-1]:
                gen = (f" gen_shed={smp[-1].get('gen_shed')} late_ticks={smp[-1].get('gen_late_ticks')}"
                       f" late_max={smp[-1].get('gen_late_max')}")
            line = " ".join(f"{k[:4]} {pct(v, .5):.0f}/{pct(v, .95):.0f}" for k, v in sorted(by.items()))
            print(f"  {inst} n={sum(len(v) for v in by.values()):5d} arr={len(subs) / span if span else 0:.2f}/s "
                  f"p50/p95 {line} bad={sum(bad.values())} cpu={cpu} by={g}{exec_spread}{gen}")
            if bad:
                print(f"     failures: {dict(bad)}")
        pj = plateau(files)
        print(f"  PLATEAU offered={pj.get('offered_rate')} achieved={pj['rate']} ratio={pj.get('achieved_ratio')} "
              f"gen_ok={pj.get('generator_ok')} cohort={pj['cohort_units']} backlog {pj['backlog_start']}->{pj['backlog_end']} "
              f"keeps_up={pj['keeps_up']} tiers={pj['sustained_tiers']} resident={pj['resident_sessions']}")
        print("  FLEET " + " ".join(f"{k[:4]} p50={pct(v, .5):.0f} p95={pct(v, .95):.0f}" for k, v in sorted(lat.items())))
        # time series, instance 1
        ev = read_evidence(files[0])
        t0 = min(u["sub"] for u in ev["units"] if u.get("sub"))
        print("  i1 per 30 s: t | arrivals/s | research p50/p95 | failed | in_flight | cpu | retrieval%")
        for w in range(0, 360, 30):
            win = [u for u in ev["units"] if u.get("sub") and w <= u["sub"] - t0 < w + 30]
            if not win:
                continue
            res = [u["lat"] / 1000 for u in win if u["sid"] == "deep_research" and u.get("ok") and u.get("lat")]
            ss = [s for s in ev["samples"] if w <= s["ts"] - t0 < w + 30]
            infl = pct([s["in_flight"] for s in ss], .5) if ss else None
            cpu = pct([s["cpu_pct"] for s in ss if s.get("cpu_pct") is not None], .5) if ss else None
            ret = pct([s["cpu_by"]["retrieval"] for s in ss if s.get("cpu_by")], .5) if ss else None
            print(f"    t={w:3d} {len(win) / 30:5.2f}/s  {pct(res, .5) or 0:5.1f}/{pct(res, .95) or 0:5.1f}  "
                  f"{sum(1 for u in win if not u.get('ok')):3d}  {infl}  {cpu}  {ret}")
    if a.probe:
        rows = [l.split() for l in open(a.probe) if l.strip()]
        if rows:
            t0 = int(rows[0][0])
            print("=== tier probe from outside the executors (t, embed code/s, rerank code/s, load):")
            print("   " + " | ".join(f"{int(r[0]) - t0}s e{r[2]}/{float(r[3]):.2f} r{r[5]}/{float(r[6]):.2f} L{r[8]}"
                                     for r in rows[::2]))
    if a.stats:
        rows = [json.loads(l) for f in glob.glob(f"{a.stats}/stats-*.jsonl") for l in open(f)]
        if rows:
            by_ts = collections.defaultdict(list)
            for r in rows:
                by_ts[int(r["ts"] // 30 * 30)].append(r)
            t0 = min(by_ts)
            print("=== executor retrieval stages per 30 s (n, p50 of process medians, max p95):")
            keys = ("embed_gate_wait_ms", "embed_call_ms", "rerank_gate_wait_ms", "rerank_call_ms",
                    "rerank_429_backoff_ms", "fuse_ms", "pack_ms", "retrieve_ms",
                    "sandbox_light_wall_ms", "sandbox_light_cpu_ms",
                    "sandbox_heavy_wall_ms", "sandbox_heavy_cpu_ms")
            for ts in sorted(by_ts):
                rs = by_ts[ts]
                parts = []
                for k in keys:
                    xs = [r[k] for r in rs if k in r]
                    if xs:
                        parts.append(f"{k[:-3]}: n={sum(x['n'] for x in xs)} "
                                     f"p50~{pct([x['p50'] for x in xs], .5):.0f} p95max={max(x['p95'] for x in xs):.0f}")
                print(f"  t+{ts - t0:4d}s procs={len(rs):3d} " + " | ".join(parts))


if __name__ == "__main__":
    main()
