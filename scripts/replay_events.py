"""Build the replay dashboard's event file from one certified series.

    PYTHONPATH=. .venv/bin/python scripts/replay_events.py <series-dir> <out.json> \
        [--mpstat set-9800-mpstat.log[.gz]] [--topology topo.txt] \
        [--rerank-cpus 35,59,...] [--embed-cpus 33,97,...] [--ingest-cpus 52,116,...] \
        [--summary data/capacity/set-.../summary.json]

The series' plateaus are laid end to end on one timeline in ladder order.
Per unit: plateau, archetype, arrival and completion offsets (seconds),
outcome, and its stage sums (model wait, retrieval, sandbox wall,
embedding) so the replay can lay the unit's lifetime out by stage. Per
two-second sample: fleet in-flight count, host CPU and memory, CPU by
process family. Per ten seconds: every physical core's occupancy (the
busier sibling) from the mpstat log, and the tier each core belongs to.
Per plateau: the offered rate, generated tokens per workflow by archetype
(from the run records), and the certified verdict from the set summary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import os
import re
import statistics as st
from collections import defaultdict

SIDS = ["task_ticket", "deep_research", "ingestion", "analyst_large", "code_agent"]
LABELS = {"task_ticket": "Task agent", "deep_research": "Research agent", "ingestion": "Ingestion agent",
          "analyst_large": "Data analyst", "code_agent": "Code agent"}
FAMILIES = ["sandbox", "retrieval", "siblings", "executors", "control", "mock_router", "database", "other"]


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def _rate_of(path):
    r = re.search(r"rate-([0-9.]+)-i(\d+)-", os.path.basename(path))
    return float(r.group(1)), int(r.group(2))


def read_ledger(path):
    rows = [json.loads(l) for l in _open(path)]
    header = next(r for r in rows if r["k"] == "header")
    footer = next((r for r in rows if r["k"] == "footer"), {})
    units = [r for r in rows if r["k"] == "unit" and r.get("r") is not None]
    samples = [r for r in rows if r["k"] == "sample"]
    return header, footer, units, samples


def parse_topology(path):
    """'cpu:siblings ' pairs -> list of 64 [t0, t1] by physical core (first sibling order)."""
    txt = open(path).read()
    pairs = re.findall(r"(\d+):(\d+),(\d+)", txt)
    cores = {}
    for cpu, a, b in pairs:
        key = tuple(sorted((int(a), int(b))))
        cores.setdefault(key, key)
    return [list(k) for k in sorted(cores)]


def parse_mpstat(path, day):
    """-> {epoch: [idle% per cpu 0..127]} for each 10 s sample."""
    out = {}
    cur = None
    for line in _open(path):
        m = re.match(r"(\d\d):(\d\d):(\d\d) (AM|PM)\s+(\S+)\s+(.*)", line)
        if not m:
            continue
        hh, mm, ss, ap, cpu, rest = m.groups()
        if cpu in ("all", "CPU"):
            if cpu == "all":
                h = int(hh) % 12 + (12 if ap == "PM" else 0)
                cur = int(dt.datetime(day.year, day.month, day.day, h, int(mm), int(ss), tzinfo=dt.timezone.utc).timestamp())
                out[cur] = [None] * 128
            continue
        if cur is None:
            continue
        fields = rest.split()
        try:
            out[cur][int(cpu)] = float(fields[-1])
        except (ValueError, IndexError):
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("out")
    ap.add_argument("--extra-series", action="append", default=[], help="another series dir whose rates join the ladder (a rung measured in a later set)")
    ap.add_argument("--mpstat", action="append", default=[], help="mpstat -P ALL log(s) covering the series; may repeat")
    ap.add_argument("--topology")
    ap.add_argument("--rerank-cpus", default="")
    ap.add_argument("--embed-cpus", default="")
    ap.add_argument("--ingest-cpus", default="")
    ap.add_argument("--summary", action="append", default=[], help="set summary.json(s) with the per-rate verdicts; may repeat")
    ap.add_argument("--instances", type=int, default=4)
    a = ap.parse_args()

    files = []
    for d in [a.series_dir, *a.extra_series]:
        files += glob.glob(os.path.join(d, "rate-*-i*-evidence-*.jsonl.gz"))
    files = sorted(files, key=_rate_of)
    by_rate = defaultdict(list)
    for f in files:
        by_rate[_rate_of(f)[0]].append(f)
    rates = sorted(by_rate)
    summary = {}
    per_rate_summary = {}
    for path in a.summary:
        sj = json.load(open(path))
        summary = summary or sj
        per_rate_summary.update(sj.get("per_rate", {}))

    topo = parse_topology(a.topology) if a.topology else [[i, i + 64] for i in range(64)]
    thread_core = {}
    for ci, (t0, t1) in enumerate(topo):
        thread_core[t0] = ci
        thread_core[t1] = ci
    tier_of_core = ["app"] * 64
    for name, arg in (("rerank", a.rerank_cpus), ("embed", a.embed_cpus), ("ingest", a.ingest_cpus)):
        for c in [int(x) for x in arg.split(",") if x.strip()]:
            tier_of_core[thread_core[c]] = name
    mp = None
    if a.mpstat:
        hdr = json.loads(next(_open(files[0])))
        day = dt.datetime.fromtimestamp(hdr["started_at"], dt.timezone.utc).date()
        mp = {}
        for path in a.mpstat:
            mp.update(parse_mpstat(path, day))
        mp_times = sorted(mp)

    plateaus, units_out, samples_out, cores_out = [], [], [], []
    offset = 0.0
    for pi, r in enumerate(rates):
        ledgers = [read_ledger(f) for f in by_rate[r]]
        t0 = min(h["started_at"] for h, _, _, _ in ledgers)
        t1 = max(float(ft.get("ended_at") or h["started_at"]) for h, ft, _, _ in ledgers)
        span = t1 - t0
        # units
        for h, ft, units, _ in ledgers:
            for u in units:
                stg = u.get("st") or {}
                def s(*keys):
                    return round(sum(float(stg[k][0]) for k in keys if k in stg) / 1000.0, 1)
                sandbox = s(*[k for k in stg if k.startswith("sandbox_") and k.endswith("_wall_ms")])
                units_out.append([pi, SIDS.index(u["sid"]) if u["sid"] in SIDS else -1,
                                  round(offset + u["sub"] - t0, 1),
                                  round(offset + u["end"] - t0, 1) if u.get("end") else None,
                                  1 if u.get("ok") else 0,
                                  s("model_wait_ms"), s("retrieve_ms"), sandbox, s("ingest_embed_ms")])
        # samples: fleet in-flight by 2 s bucket, host figures from instance 1
        infl = defaultdict(float)
        host = {}
        for h, ft, _, samples in ledgers:
            inst = _rate_of(by_rate[r][ledgers.index((h, ft, _, samples))])[1] if False else None
            for smp in samples:
                b = int((smp["ts"] - t0) // 2)
                infl[b] += smp.get("in_flight") or 0
                if smp.get("cpu_pct") is not None and b not in host:
                    fam = smp.get("cpu_by") or {}
                    host[b] = [round(smp["cpu_pct"], 1), round(smp.get("mem_gb") or 0, 1),
                               [round(float(fam.get(k) or 0), 1) for k in FAMILIES]]
        for b in sorted(infl):
            hv = host.get(b) or [None, None, None]
            samples_out.append([pi, round(offset + b * 2, 1), int(infl[b]), hv[0], hv[1], hv[2]])
        # cores per 10 s
        if mp:
            slots = int(span // 10) + 1
            grid = []
            for k in range(slots):
                target = t0 + k * 10
                # nearest mpstat sample at or after target
                ts = next((x for x in mp_times if x >= target), None)
                if ts is None or ts - target > 15:
                    grid.append(None)
                    continue
                idle = mp[ts]
                row = []
                for (ta, tb) in topo:
                    ia, ib = idle[ta], idle[tb]
                    busy = max(100 - (ia if ia is not None else 100), 100 - (ib if ib is not None else 100))
                    row.append(int(round(busy)))
                grid.append(row)
            cores_out.append(grid)
        # per-plateau record
        caps = sorted(c for d in [a.series_dir, *a.extra_series] for c in glob.glob(os.path.join(d, f"rate-{r:g}-i*-capacity-*.json")))
        tok = defaultdict(lambda: [0, 0])
        out_total = 0
        in_total = 0
        calls_total = 0
        for cp in caps:
            d = json.load(open(cp))
            out_total += d.get("total_tokens_out") or 0
            in_total += d.get("total_tokens_in") or 0
            calls_total += sum((row.get("calls") or 0) for row in (d.get("per_scenario") or {}).values())
            for sid, row in (d.get("per_scenario") or {}).items():
                tok[sid][0] += row.get("tokens_out") or 0
                tok[sid][1] += row.get("calls") or 0
        gen_per_wf = {sid: (round(v[0] / v[1]) if v[1] else None) for sid, v in tok.items()}
        srow = per_rate_summary.get(f"{r:g}") or per_rate_summary.get(str(r)) or {}
        plateaus.append({"rate_per_instance": r, "rate": round(r * a.instances, 2), "t0": round(offset, 1),
                         "t1": round(offset + span, 1), "gen_tokens_per_wf": gen_per_wf,
                         "gen_tokens_total": out_total, "prompt_tokens_total": in_total,
                         "workflows_completed": calls_total, "span_s": round(span, 1),
                         "resident": srow.get("resident_measured_median") or srow.get("resident_median"),
                         "keeps_up": bool(srow.get("keeps_up_all")) if srow else None})
        offset += span

    out = {"meta": {"series": os.path.basename(a.series_dir.rstrip("/")), "set": summary.get("set"),
                    "sids": SIDS, "labels": [LABELS[s] for s in SIDS], "families": FAMILIES,
                    "tile": {"code_agent": 2, "analyst_large": 2, "deep_research": 1, "ingestion": 1, "task_ticket": 6},
                    "tiers": tier_of_core, "duration": round(offset, 1),
                    "unit_fields": ["plateau", "sid", "t_sub", "t_end", "ok", "model_wait_s", "retrieval_s", "sandbox_wall_s", "embed_s"],
                    "sample_fields": ["plateau", "t", "in_flight", "host_cpu_pct", "mem_gb", "cpu_by_family"]},
           "plateaus": plateaus, "units": units_out, "samples": samples_out, "cores": cores_out}
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"{a.out}: {len(units_out)} units, {len(samples_out)} samples, {sum(len(g) for g in cores_out)} core slots, "
          f"{os.path.getsize(a.out) / 1e6:.2f} MB, {offset:.0f} s")


if __name__ == "__main__":
    main()
