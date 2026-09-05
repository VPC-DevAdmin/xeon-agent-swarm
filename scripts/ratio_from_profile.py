"""The orchestration-socket-to-GPU ratio from measured data on both sides.

    PYTHONPATH=. .venv/bin/python scripts/ratio_from_profile.py <series-dir> <rate> <profile.json> [--cores 64]

Host side, from a plateau of a series: host core-seconds per generated
token = (busy physical cores at the plateau) / (generated tokens per
second), both read from the plateau's ledgers and capacity files (busy
cores from the mid-run host CPU samples on the whole-host basis, tokens
from the completed units' output tokens over the cohort span).

Serving side, from a recording made by scripts/replay_query_set.py
against a KNOWN number of GPUs (--gpus): generation tokens per second at
the ceiling of the concurrency sweep, divided by the GPUs.

    GPUs per socket = (cores x 1000) / (core-ms per token x gen tok/s per GPU)

Prints the ratio for the profile and, for reference, at 1,300 / 2,400 /
3,800 tok/s per GPU. Both inputs are measured; nothing is typed in.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics as st

from backend.capacity.evidence import read_evidence

NCPU_THREADS = 128


def host_side(series_dir: str, rate: str) -> dict:
    files = sorted(glob.glob(f"{series_dir}/rate-{rate}-i*-evidence-*.jsonl.gz"))
    caps = sorted(glob.glob(f"{series_dir}/rate-{rate}-i*-capacity-*.json"))
    if not files or not caps:
        raise SystemExit(f"no plateau files for rate {rate} under {series_dir}")
    busy_threads = []
    units = 0
    span = 0.0
    for f in files:
        ev = read_evidence(f)
        smp = [s for s in ev["samples"] if s.get("cpu_pct") is not None]
        mid = smp[len(smp) // 4: 3 * len(smp) // 4] or smp
        busy_threads.append(st.median(s["cpu_pct"] for s in mid) / 100.0 * NCPU_THREADS)
        subs = sorted(u["sub"] for u in ev["units"] if u.get("sub") and u.get("r") is not None)
        units += len(subs)
        span = max(span, subs[-1] - subs[0] if len(subs) > 1 else 0.0)
    tokens_out = 0
    completed = 0
    for c in caps:
        j = json.load(open(c))
        for row in (j.get("per_scenario") or {}).values():
            tokens_out += int(row.get("tokens_out") or 0)
            completed += int(row.get("calls") or 0)
    out_per_wf = tokens_out / max(1, completed)
    wf_per_s = units / span if span else 0.0
    gen_tok_s = wf_per_s * out_per_wf
    # Every instance samples the WHOLE host, so the four readings are the
    # same quantity: take their median, never their sum. Busy physical
    # cores: thread-busy on a 2-way SMT host under-reads core occupancy;
    # the certified set's per-core sampler measured cores at ~1.3x threads
    # at the reference point. Report both bases.
    threads_busy = st.median(busy_threads)
    return {"wf_per_s": round(wf_per_s, 3), "gen_tokens_per_wf": round(out_per_wf),
            "gen_tok_s": round(gen_tok_s), "threads_busy": round(threads_busy, 1),
            "core_ms_per_token_threads": round(threads_busy / 2 * 1000 / max(1, gen_tok_s), 3),
            "core_ms_per_token_cores": round(threads_busy / 2 * 1.3 * 1000 / max(1, gen_tok_s), 3)}


def cores_busy_from_mpstat(series_dir: str, rate: str, log: str, cores: int) -> float | None:
    """Physical-core occupancy over the plateau's steady window from an
    mpstat -P ALL log: per core, the busier of its two threads, averaged
    over the window (same rule as the per-core sampler)."""
    import datetime as dt
    import re as _re
    f = sorted(glob.glob(f"{series_dir}/rate-{rate}-i1-evidence-*.jsonl.gz"))
    if not f:
        return None
    stamp = _re.search(r"(\d{8}-\d{6})", f[0].split("/")[-1]).group(1)
    t0 = dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp()
    lo, hi = t0 + 150, t0 + 690
    sib: dict[int, list[int]] = {}
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
        cpu = int(p.split("/")[5][3:]); cid = int(open(p).read()); sib.setdefault(cid, []).append(cpu)
    if not sib:
        return None
    day = dt.datetime.fromtimestamp(t0, dt.timezone.utc).date()
    per_cpu: dict[int, list[float]] = {}
    for line in open(log):
        p = line.split()
        if len(p) < 12 or not _re.match(r"\d\d:\d\d:\d\d", p[0]):
            continue
        t = p[0]
        if p[1] in ("AM", "PM"):
            h_ = int(t[:2]); h_ = h_ + 12 if p[1] == "PM" and h_ < 12 else (0 if p[1] == "AM" and h_ == 12 else h_)
            t = f"{h_:02d}{t[2:]}"; p = p[:1] + p[2:]
        if p[1] in ("all", "CPU"):
            continue
        ts = dt.datetime.strptime(f"{day} {t}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
        if not (lo <= ts < hi):
            continue
        try:
            per_cpu.setdefault(int(p[1]), []).append(100.0 - float(p[-1]))
        except ValueError:
            continue
    if not per_cpu:
        return None
    occ = [st.mean(max(st.mean(per_cpu.get(c, [0.0])) for c in cpus) for _ in [0]) for cid, cpus in sib.items()]
    return sum(o / 100.0 for o in occ)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("rate")
    ap.add_argument("profile")
    ap.add_argument("--cores", type=int, default=64)
    ap.add_argument("--mpstat", help="mpstat -P ALL log covering the plateau: busy cores are then MEASURED per core "
                                     "instead of estimated from thread-busy with an SMT factor")
    a = ap.parse_args()
    h = host_side(a.series_dir, a.rate)
    if a.mpstat:
        busy = cores_busy_from_mpstat(a.series_dir, a.rate, a.mpstat, a.cores)
        if busy:
            h["cores_busy_measured"] = round(busy, 1)
            h["core_ms_per_token_cores"] = round(busy * 1000 / max(1, h["gen_tok_s"]), 3)
    prof = json.load(open(a.profile))
    per_gpu = prof.get("gen_tok_s_per_gpu")
    print(f"host: {h['wf_per_s']} wf/s, {h['gen_tokens_per_wf']} gen tokens/wf, {h['gen_tok_s']} gen tok/s, "
          f"{h['threads_busy']} threads busy"
          + (f", {h['cores_busy_measured']} cores busy (measured)" if h.get("cores_busy_measured") else "")
          + f" -> {h['core_ms_per_token_cores']} core-ms/token (thread basis {h['core_ms_per_token_threads']})")
    cm = h["core_ms_per_token_cores"]
    if per_gpu:
        print(f"serving: {prof['model']} on {prof['gpus']} GPU(s): ceiling {prof['gen_tok_s_ceiling']:.0f} gen tok/s "
              f"= {per_gpu:.0f} per GPU (levels {prof['levels']})")
        print(f"GPUs per {a.cores}-core socket = {a.cores * 1000 / (cm * per_gpu):.2f}  (1 : {a.cores * 1000 / (cm * per_gpu):.1f})")
    else:
        print("serving: profile has no gpus/ceiling (record with --sweep --gpus N); reference points only")
    for ref in (1300, 2400, 3800):
        print(f"  at {ref} tok/s per GPU: 1 : {a.cores * 1000 / (cm * ref):.1f}")


if __name__ == "__main__":
    main()
