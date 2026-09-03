"""Physical-core occupancy from an mpstat -P ALL log: a core is as busy as
its busier SMT sibling. Groups by the tier allocation."""
import sys, glob, re, statistics as st
log, alloc = sys.argv[1], sys.argv[2]
cores = {}
for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
    cpu = int(p.split("/")[5][3:]); cid = int(open(p).read()); cores.setdefault(cid, []).append(cpu)
def cpuset(s):
    out = set()
    for part in s.split(","):
        if "-" in part: a, b = part.split("-"); out.update(range(int(a), int(b) + 1))
        elif part: out.add(int(part))
    return out
env = dict(l.strip().split("=", 1) for l in open(alloc) if "=" in l)
rer = cpuset(env["RERANK_CPUS"]); emb = cpuset(env["EMBED_CPUS"]); rest = cpuset(env["REST_CPUS"])
group_of_core = {}
for cid, cpus in cores.items():
    g = "reranker" if any(c in rer for c in cpus) else "embedder" if any(c in emb for c in cpus) else "rest"
    group_of_core[cid] = g
# parse mpstat blocks: lines "HH:MM:SS  CPU  %usr ... %idle"
blocks = []; cur = {}; cpu_col = None
for line in open(log):
    f = line.split()
    if not f: continue
    if f[0] == "Average:":
        break
    if "CPU" in f and "%idle" in f:               # header: find the CPU column
        cpu_col = f.index("CPU"); continue
    if cpu_col is None or len(f) <= cpu_col: continue
    tag = f[cpu_col]
    if tag == "all":                              # an "all" line OPENS each interval's block
        if cur: blocks.append(cur)
        cur = {}
    elif tag.isdigit():
        cur[int(tag)] = 100.0 - float(f[-1])
if cur: blocks.append(cur)
print(f"intervals parsed: {len(blocks)} (5 s each)")
for i, b in enumerate(blocks):
    if i % 6 == 0:
        rr = st.mean(max(b.get(c, 0.0) for c in cores[cid]) for cid in cores)
        print(f"  t+{i*5:3d}s box core-occupancy {rr:5.1f}%")
mid = blocks[len(blocks)//4: 3*len(blocks)//4] or blocks
per_core_busy = {cid: st.median([max(b.get(c, 0.0) for c in cpus) for b in mid]) for cid, cpus in cores.items()}
per_core_threads = {cid: st.median([sum(b.get(c, 0.0) for c in cpus) / len(cpus) for b in mid]) for cid, cpus in cores.items()}
for g in ("reranker", "embedder", "rest"):
    ids = [cid for cid in cores if group_of_core[cid] == g]
    busy = [per_core_busy[c] for c in ids]; thr = [per_core_threads[c] for c in ids]
    print(f"{g:9s} cores={len(ids):2d} core-occupancy(max sibling) mean={st.mean(busy):5.1f}% min={min(busy):5.1f}% | thread-capacity used mean={st.mean(thr):5.1f}%")
allb = list(per_core_busy.values()); allt = list(per_core_threads.values())
print(f"BOX       cores={len(allb)} core-occupancy mean={st.mean(allb):.1f}%  thread-capacity {st.mean(allt):.1f}%  (samples {len(mid)} of {len(blocks)})")
