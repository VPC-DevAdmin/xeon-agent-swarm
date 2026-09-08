"""Host work per generated token for a plateau, and the CPU:GPU ratio.

Host side, from the run records: core-ms per generated token = busy
physical cores over the steady window of the plateau (mpstat, per core the
busier sibling, averaged from 600 s after the rung starts to 1500 s, past
the transient) divided by generated tokens per second. Generated tokens
per second are the achieved workflow rate times the DECLARED MIX's output
per workflow: each archetype's output per completed workflow (its planner
and worker calls) plus its model-based judgments' outputs, weighted by the
tile. The completed cohort of a finite hold over-represents the short
workflows, so a completion-weighted average understates the mix; it is
printed beside the figure of record for comparison.

Serving side, from a recording made by scripts/replay_query_set.py
against a KNOWN number of GPUs (--gpus): generation tokens per second at
the ceiling of the concurrency sweep, divided by the GPUs.

    GPUs per socket = (cores x 1000) / (core-ms per token x gen tok/s per GPU)

Prints the ratio for the profile and, for reference, at 2,400 / 3,500 /
4,378 tok/s per GPU (3,500 is the record: gpt-oss-20b on one RTX PRO
6000 under vLLM, context-adjusted; see docs/benchmark-methodology.md
section 11).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics as st

from backend.capacity.evidence import read_evidence

NCPU_THREADS = 128


TILE_DEFAULT = {"task_ticket": 6, "code_agent": 2, "analyst_large": 2, "deep_research": 1, "ingestion": 1}
# Model-based judgments per workflow (one per worker plus the synthesis) and
# their mean output tokens in the calibrated profile (gpt-oss-20b, low).
JUDGE_CALLS = {"task_ticket": 2, "ingestion": 2, "code_agent": 4, "analyst_large": 4, "deep_research": 4}
JUDGE_TOKENS_DEFAULT = {"task_ticket": 66, "ingestion": 83, "code_agent": 103, "analyst_large": 78, "deep_research": 105}


def judge_tokens(profile_path: str | None) -> dict:
    """Mean judge output tokens per archetype from a recorded profile, else the
    calibrated defaults."""
    if not profile_path or not os.path.exists(profile_path):
        return dict(JUDGE_TOKENS_DEFAULT)
    acc: dict[str, list] = {}
    with open(profile_path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ok") and str(r.get("key", "")).endswith("/judge/0"):
                acc.setdefault(r["archetype"], []).append(int(r.get("completion_tokens") or 0))
    out = dict(JUDGE_TOKENS_DEFAULT)
    out.update({k: st.mean(v) for k, v in acc.items() if v})
    return out


def host_side(series_dir: str, rate: str, tile: dict | None = None, judge: dict | None = None) -> dict:
    """Host side of a plateau. Generated tokens per second are the DECLARED
    MIX times the per-archetype output per completed workflow, plus the
    model-based judgments' outputs: at a plateau that keeps up, completions
    per archetype equal arrivals per archetype, whereas the completed cohort
    of a finite hold over-represents the short workflows (the long ones are
    still in flight when it ends)."""
    tile = tile or TILE_DEFAULT
    judge = judge or JUDGE_TOKENS_DEFAULT
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
    per_sid: dict[str, list] = {}
    tokens_out = 0
    completed = 0
    for c in caps:
        j = json.load(open(c))
        for sid, row in (j.get("per_scenario") or {}).items():
            acc = per_sid.setdefault(sid, [0, 0])
            acc[0] += int(row.get("tokens_out") or 0)
            acc[1] += int(row.get("calls") or 0)
            tokens_out += int(row.get("tokens_out") or 0)
            completed += int(row.get("calls") or 0)
    completion_weighted = tokens_out / max(1, completed)
    weight = sum(tile.values())
    per_arch = {sid: (per_sid[sid][0] / per_sid[sid][1] if sid in per_sid and per_sid[sid][1] else 0.0)
                + JUDGE_CALLS.get(sid, 0) * judge.get(sid, 0) for sid in tile}
    out_per_wf = sum(tile[sid] * per_arch[sid] for sid in tile) / weight
    wf_per_s = units / span if span else 0.0
    gen_tok_s = wf_per_s * out_per_wf
    # Every instance samples the WHOLE host, so the four readings are the
    # same quantity: take their median, never their sum. Busy physical
    # cores: thread-busy on a 2-way SMT host under-reads core occupancy;
    # the certified set's per-core sampler measured cores at ~1.3x threads
    # at the reference point. Report both bases.
    threads_busy = st.median(busy_threads)
    return {"wf_per_s": round(wf_per_s, 3), "gen_tokens_per_wf": round(out_per_wf),
            "gen_tokens_per_wf_completion_weighted": round(completion_weighted),
            "gen_tokens_per_arch": {k: round(v) for k, v in per_arch.items()},
            "gen_tok_s": round(gen_tok_s), "threads_busy": round(threads_busy, 1),
            "core_ms_per_token_threads": round(threads_busy / 2 * 1000 / max(1, gen_tok_s), 3),
            "core_ms_per_token_cores": round(threads_busy / 2 * 1.3 * 1000 / max(1, gen_tok_s), 3)}


def cores_busy_from_mpstat(series_dir: str, rate: str, log: str, cores: int, window=(600.0, 1500.0)) -> float | None:
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
    lo, hi = t0 + window[0], t0 + window[1]
    sib: dict[int, list[int]] = {}
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
        cpu = int(p.split("/")[5][3:]); cid = int(open(p).read()); sib.setdefault(cid, []).append(cpu)
    if not sib:
        return None
    day = dt.datetime.fromtimestamp(t0, dt.timezone.utc).date()
    per_cpu: dict[tuple[str, int], float] = {}
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
            per_cpu[(t, int(p[1]))] = 100.0 - float(p[-1])
        except ValueError:
            continue
    if not per_cpu:
        return None
    # Per sample, a core is as busy as its busier thread; average those
    # over the window (the per-core sampler's convention).
    stamps = sorted({t for t, _ in per_cpu})
    total = 0.0
    for cid, cpus in sib.items():
        total += st.mean(max(per_cpu.get((t, c), 0.0) for c in cpus) for t in stamps) / 100.0
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("series_dir")
    ap.add_argument("rate")
    ap.add_argument("profile")
    ap.add_argument("--cores", type=int, default=64)
    ap.add_argument("--mpstat", help="mpstat -P ALL log covering the plateau: busy cores are then MEASURED per core "
                                     "instead of estimated from thread-busy with an SMT factor")
    ap.add_argument("--window", default="600:1500",
                    help="seconds after the rung starts over which busy cores are averaged; the default skips the "
                         "transient (the slowest archetype takes about nine minutes to reach steady state)")
    ap.add_argument("--judge-profile", default="data/capacity/serving/gptoss20b-low-faithful/calls.jsonl",
                    help="recorded profile from which the judges' output tokens per archetype are taken")
    a = ap.parse_args()
    lo, hi = (float(x) for x in a.window.split(":"))
    h = host_side(a.series_dir, a.rate, judge=judge_tokens(a.judge_profile))
    if a.mpstat:
        busy = cores_busy_from_mpstat(a.series_dir, a.rate, a.mpstat, a.cores, window=(lo, hi))
        if busy:
            h["cores_busy_measured"] = round(busy, 1)
            h["core_ms_per_token_cores"] = round(busy * 1000 / max(1, h["gen_tok_s"]), 3)
    prof = json.load(open(a.profile))
    per_gpu = prof.get("gen_tok_s_per_gpu")
    print(f"host: {h['wf_per_s']} wf/s, {h['gen_tokens_per_wf']} gen tokens/wf (declared mix, judgments included; "
          f"{h['gen_tokens_per_wf_completion_weighted']} completion-weighted), {h['gen_tok_s']} gen tok/s, "
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
    for ref in (2400, 3500, 4378):
        print(f"  at {ref} tok/s per GPU: 1 : {a.cores * 1000 / (cm * ref):.1f}")


if __name__ == "__main__":
    main()
