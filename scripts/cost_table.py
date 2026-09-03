"""Per-component cost of the workload from a plateau series: core-seconds
per workflow for the sandbox, the reranker, and orchestration, read from
the ledgers' CPU attribution and the executors' stage stats.

    PYTHONPATH=. .venv/bin/python scripts/cost_table.py data/capacity/series-<seed>-<stamp> [--stats data/capacity/retrieval]

Physical-core seconds are logical-thread seconds for single-threaded work
(a sandbox job or an executor holds one thread); the reranker's share is
one thread per core by design, so its logical share IS its core share.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import statistics as st

from backend.capacity.evidence import read_evidence

NCPU = 128


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("--stats")
    a = ap.parse_args()
    rates = sorted({int(re.search(r"rate-(\d+)-i", f).group(1))
                    for f in glob.glob(f"{a.series_dir}/rate-*-i*-evidence-*.jsonl.gz")})
    print("| per-instance rate | fleet wf/s | sandbox core-s/wf | reranker core-s/wf, consumed (reserved) | orchestration core-s/wf (executors+control+routers+db+other) | host threads busy |")
    print("|---|---|---|---|---|---|")
    for r in rates:
        files = sorted(glob.glob(f"{a.series_dir}/rate-{r}-i*-evidence-*.jsonl.gz"))
        groups = collections.defaultdict(list)
        cpu = []
        n_units = 0
        span = 0.0
        for f in files:
            ev = read_evidence(f)
            subs = sorted(u["sub"] for u in ev["units"] if u.get("sub") and u.get("r") is not None)
            n_units += len(subs)
            span = max(span, (subs[-1] - subs[0]) if len(subs) > 1 else 0.0)
            smp = [s for s in ev["samples"] if s.get("cpu_pct") is not None]
            mid = smp[len(smp) // 4: 3 * len(smp) // 4] or smp
            cpu.append(st.median([s["cpu_pct"] for s in mid]))
            for k in ("executors", "siblings", "control", "mock_router", "retrieval", "sandbox", "other"):
                groups[k].append(st.median([(s.get("cpu_by") or {}).get(k, 0.0) for s in mid]))
        fleet_rate = n_units / span if span else 0.0
        g = {k: st.median(v) for k, v in groups.items()}
        # instance 1's ledger sees its own executors + the siblings; sandbox is fleet-wide already
        threads = lambda pct: pct / 100.0 * NCPU
        sandbox = threads(g["sandbox"]) / fleet_rate if fleet_rate else 0.0
        # The tier's attributed share is RESERVED capacity (its runtime
        # threads spin on their cores whatever the load); its consumed cost
        # is demand over the measured pair budget: 35 scored pairs per
        # core-second, 16 pairs per call, (3+1)/6 calls per tile-weighted
        # workflow at v17.
        calls_per_wf = (3 + 1) / 6.0
        rerank = calls_per_wf * 16 / 35.0
        reserved = threads(g["retrieval"]) / fleet_rate if fleet_rate else 0.0
        orch = threads(g["executors"] + g["siblings"] + g["control"] + g["mock_router"] + g["other"]) / fleet_rate if fleet_rate else 0.0
        print(f"| {r}/s | {fleet_rate:.1f} | {sandbox:.2f} | {rerank:.2f} (tier reserved {reserved:.2f}) | {orch:.2f} | {st.median(cpu):.0f}% |")
    if a.stats:
        rows = [json.loads(l) for f in glob.glob(f"{a.stats}/stats-*.jsonl") for l in open(f)]
        for k in ("sandbox_light_cpu_ms", "sandbox_heavy_cpu_ms", "sandbox_light_wall_ms", "sandbox_heavy_wall_ms", "rerank_call_ms"):
            xs = [row[k]["p50"] for row in rows if k in row]
            if xs:
                print(f"{k}: median of process medians {st.median(xs):.0f} ms over {len(xs)} flushes")


if __name__ == "__main__":
    main()
