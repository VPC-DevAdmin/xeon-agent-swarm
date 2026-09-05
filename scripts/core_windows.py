"""Per-core occupancy over a series' plateau windows from an mpstat log.

    python3 scripts/core_windows.py <series-dir> <mpstat-log> [allocation.env]

For each rate in the series (window: evidence stamp +150 s to +690 s), a
physical core's occupancy per sample is the busier of its two threads,
and the figure reported is the MEAN over the window's samples: the
time-average of busy cores, which is what a ratio needs. (An earlier
version took the median per core, which reads a core busy 55% of the
time as 100% and overstated occupancy by 5 to 10 points.) Groups follow
the allocation file when given.
"""
import datetime as dt
import glob
import os
import re
import statistics as st
import sys

series, log = sys.argv[1], sys.argv[2]
alloc = sys.argv[3] if len(sys.argv) > 3 else "data/capacity/retrieval/allocation.env"
cores: dict[int, list[int]] = {}
for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
    cpu = int(p.split("/")[5][3:]); cid = int(open(p).read()); cores.setdefault(cid, []).append(cpu)


def cpuset(s):
    out = set()
    for part in (s or "").split(","):
        if "-" in part:
            a, b = part.split("-"); out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out


env = {}
if os.path.exists(alloc):
    env = dict(l.strip().split("=", 1) for l in open(alloc) if "=" in l)
groups = {"reranker": cpuset(env.get("RERANK_CPUS")), "embedder": cpuset(env.get("EMBED_CPUS")),
          "ingest": cpuset(env.get("INGEST_EMBED_CPUS"))}
def grp(cpus):
    for g, s in groups.items():
        if s and any(c in s for c in cpus):
            return g
    return "rest"
gof = {cid: grp(cpus) for cid, cpus in cores.items()}

blocks = []; cur = {}; cur_ts = None; cpu_col = None; day = None
for line in open(log):
    f = line.split()
    if not f:
        continue
    if f[0] == "Linux":
        day = f[3]; continue
    if f[0] == "Average:":
        break
    if "CPU" in f and "%idle" in f:
        cpu_col = f.index("CPU"); continue
    if cpu_col is None or len(f) <= cpu_col:
        continue
    tag = f[cpu_col]
    if tag == "all":
        if cur:
            blocks.append((cur_ts, cur))
        cur = {}
        try:
            cur_ts = dt.datetime.strptime(f"{day} {f[0]} {f[1]}", "%m/%d/%Y %I:%M:%S %p").timestamp()
        except ValueError:
            cur_ts = dt.datetime.strptime(f"{day} {f[0]}", "%m/%d/%Y %H:%M:%S").timestamp()
    elif tag.isdigit():
        cur[int(tag)] = 100.0 - float(f[-1])
if cur:
    blocks.append((cur_ts, cur))

rates = []
for f in glob.glob(f"{series}/rate-*-i1-evidence-*.jsonl.gz"):
    b = f.split("/")[-1]
    r = re.search(r"rate-([0-9.]+)-", b).group(1)
    t = dt.datetime.strptime(re.search(r"(\d{8}-\d{6})", b).group(1), "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp()
    rates.append((float(r), r, t + 150, t + 690))
for _, r, a, b in sorted(rates):
    sel = [blk for ts, blk in blocks if a <= ts <= b]
    if not sel:
        print(f"rate {r}/s: no samples"); continue
    occ = {cid: st.mean(max(blk.get(c, 0.0) for c in cpus) for blk in sel) for cid, cpus in cores.items()}
    thr = {cid: st.mean(sum(blk.get(c, 0.0) for c in cpus) / len(cpus) for blk in sel) for cid, cpus in cores.items()}
    parts = []
    for g in ("reranker", "embedder", "ingest", "rest"):
        ids = [cid for cid in cores if gof[cid] == g]
        if ids:
            parts.append(f"{g} {len(ids)}c occ {st.mean(occ[c] for c in ids):.0f}% thr {st.mean(thr[c] for c in ids):.0f}%")
    print(f"rate {r}/s: samples={len(sel)} box occupancy {st.mean(occ.values()):.1f}% ({st.mean(occ.values()) / 100 * len(cores):.1f} cores; threads {st.mean(thr.values()):.1f}%) | " + " | ".join(parts))
