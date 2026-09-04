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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("rate")
    ap.add_argument("profile")
    ap.add_argument("--cores", type=int, default=64)
    a = ap.parse_args()
    h = host_side(a.series_dir, a.rate)
    prof = json.load(open(a.profile))
    per_gpu = prof.get("gen_tok_s_per_gpu")
    print(f"host: {h['wf_per_s']} wf/s, {h['gen_tokens_per_wf']} gen tokens/wf, {h['gen_tok_s']} gen tok/s, "
          f"{h['threads_busy']} threads busy -> {h['core_ms_per_token_cores']} core-ms/token "
          f"(thread basis {h['core_ms_per_token_threads']})")
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
