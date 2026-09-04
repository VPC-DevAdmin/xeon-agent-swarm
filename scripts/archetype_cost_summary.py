"""Per-archetype core-seconds per workflow from single-archetype plateaus
at two rates: slope of busy hardware threads against delivered rate, by
attribution group, with the reranker's consumed cost from its pair law.

    PYTHONPATH=. .venv/bin/python scripts/archetype_cost_summary.py data/capacity/archetypes-<seed>-<stamp>
"""
import collections, glob, json, re, statistics as st, sys
from pathlib import Path
from backend.capacity.evidence import read_evidence

NCPU = 128
RETRIEVALS = {"research_brief": 3, "comparison": 1, "digest": 0, "data_analysis": 0, "task_ticket": 0}
# Heavy mix: retrievals per workflow and the declared rerank depth per call
# (the reference archetypes rerank at depth 16).
RETRIEVALS.update({"code_agent": 0, "analyst_xl": 0, "ingestion": 0, "deep_research": 3})
DEPTH = {"deep_research": 128}
JOBS = {"research_brief": (0, 0), "comparison": (1, 0), "digest": (0, 0), "data_analysis": (0, 3), "task_ticket": (0, 0)}  # (light, heavy)



def _rate_of(path: str) -> float:
    """Per-instance rate from a series file name; integers stay integers
    (rate-8-i1 -> 8), fractional rates are floats (rate-0.5-i1 -> 0.5)."""
    import re as _re
    r = _re.search(r"rate-([0-9.]+)-i", path).group(1)
    return int(r) if r.isdigit() else float(r)

def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def plateau_point(series_dir, r):
    files = sorted(glob.glob(f"{series_dir}/rate-{r}-i*-evidence-*.jsonl.gz"))
    busy = collections.defaultdict(list); n = 0; span = 0.0; lat = []
    for f in files:
        ev = read_evidence(f)
        subs = sorted(u["sub"] for u in ev["units"] if u.get("sub") and u.get("r") is not None)
        n += len(subs); span = max(span, subs[-1] - subs[0] if len(subs) > 1 else 0.0)
        lat += [u["lat"] / 1000 for u in ev["units"] if u.get("ok") and u.get("lat")]
        smp = [s for s in ev["samples"] if s.get("cpu_pct") is not None]
        mid = smp[len(smp) // 4: 3 * len(smp) // 4] or smp
        busy["host"].append(st.median([s["cpu_pct"] for s in mid]) / 100 * NCPU)
        for k in ("executors", "siblings", "control", "mock_router", "retrieval", "sandbox", "other"):
            busy[k].append(st.median([(s.get("cpu_by") or {}).get(k, 0.0) for s in mid]) / 100 * NCPU)
    rate = n / span if span else 0.0
    return rate, {k: st.median(v) for k, v in busy.items()}, pct(lat, .5), pct(lat, .95)


def main():
    d = Path(sys.argv[1])
    rows = [l.split() for l in (d / "series.txt").read_text().splitlines() if l.strip()]
    print("| archetype | p50 / p95 (s) | host core-s/wf | of which sandbox | reranker (pair law) | executors + search + records + orchestration | notes |")
    print("|---|---|---|---|---|---|---|")
    out = {}
    for sid, sdir in rows:
        rates = sorted({_rate_of(f) for f in glob.glob(f"{sdir}/rate-*-i*-evidence-*.jsonl.gz")})
        if len(rates) < 2:
            print(f"| {sid} | (one rate only) |"); continue
        (r1, b1, p50, p95), (r2, b2, _, _) = plateau_point(sdir, rates[0]), plateau_point(sdir, rates[-1])
        dr = r2 - r1
        slope = {k: (b2[k] - b1[k]) / dr for k in b1} if dr > 0 else {k: 0.0 for k in b1}
        rerank = RETRIEVALS.get(sid, 0) * DEPTH.get(sid, 16) / 35.0
        orch = slope["executors"] + slope["siblings"] + slope["control"] + slope["mock_router"] + slope["other"]
        total = slope["host"] - slope["retrieval"] + rerank   # tier share is reserved; replace by consumed
        out[sid] = {"p50_s": p50, "p95_s": p95, "host_core_s_per_wf": round(total, 2), "sandbox": round(slope["sandbox"], 2),
                    "reranker_consumed": round(rerank, 2), "executor_side": round(orch, 2), "rates": [r1, r2]}
        print(f"| {sid} | {p50:.0f} / {p95:.0f} | {total:.2f} | {slope['sandbox']:.2f} | {rerank:.2f} | {orch:.2f} | slope over {r1:.1f} to {r2:.1f} wf/s |")
    (d / "summary.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
