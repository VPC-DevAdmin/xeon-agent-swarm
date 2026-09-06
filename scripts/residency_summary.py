"""Summarise a residency photograph (scripts/residency_photo.sh).

    PYTHONPATH=. .venv/bin/python scripts/residency_summary.py data/capacity/photo-<seed>-<stamp>

Over the hold window (the ledger's window marker to the run's end): agents
resident (the fleet's in-flight samples), completions per second, per-type
latency p50 / p95, drift between the two halves of the hold, host CPU, and
the Little's-law check (resident = rate x (mean latency + think)).
"""
import glob
import json
import statistics as st
import sys

from backend.capacity.evidence import read_evidence


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def main(d):
    ledgers = sorted(glob.glob(f"{d}/i*-evidence-*.jsonl.gz"))
    caps = sorted(glob.glob(f"{d}/i*-capacity-*.json"))
    users = sum(int(json.load(open(c)).get("peak_users") or 0) for c in caps)
    a, b = None, None
    all_units, infl, cpu, mem = [], {}, [], []
    per_ledger = []
    for path in ledgers:
        ev = read_evidence(path)
        w = ev["windows"][0] if ev.get("windows") else None
        # fall back: the hold is the last HOLD seconds before the footer
        ft = ev.get("footer") or {}
        end = float(ft.get("ended_at") or max(u["end"] for u in ev["units"] if u.get("end")))
        start = float(w["a"]) if w and w.get("a") else end - 600.0
        a = start if a is None else max(a, start)
        b = end if b is None else min(b, end)
        per_ledger.append((ev, start, end))
    span = b - a
    for ev, _, _ in per_ledger:
        all_units += [u for u in ev["units"] if u.get("end") and a <= u["end"] <= b]
        for s in ev["samples"]:
            if a <= s["ts"] <= b:
                k = int((s["ts"] - a) // 2)
                infl[k] = infl.get(k, 0) + (s.get("in_flight") or 0)
                if s.get("cpu_pct") is not None and s.get("cpu_pct") and len(cpu) < 10_000:
                    cpu.append(s["cpu_pct"]); mem.append(s.get("mem_gb") or 0)
    resident = st.median(infl.values()) if infl else None
    rate = len(all_units) / span if span > 0 else 0.0
    sids = sorted({u["sid"] for u in all_units})
    lines = [f"# Residency photograph {d}", "",
             f"Sessions held: {users} across {len(ledgers)} instances; hold {span:.0f} s.", "",
             "| agents resident (median of samples) | completions / s | host threads busy | host memory |",
             "|---|---|---|---|",
             f"| {resident:.0f} | {rate:.2f} | {st.median(cpu):.0f}% | {st.median(mem):.0f} GB |" if cpu else f"| {resident} | {rate:.2f} | – | – |",
             "", "| archetype | n | p50 s | p95 s | first-half p50 | second-half p50 | drift |", "|---|---|---|---|---|---|---|"]
    mid = a + span / 2
    mean_lat = []
    for sid in sids:
        xs = [u for u in all_units if u["sid"] == sid and u.get("ok") and u.get("lat")]
        l1 = [u["lat"] / 1000 for u in xs if u["end"] < mid]; l2 = [u["lat"] / 1000 for u in xs if u["end"] >= mid]
        lat = [u["lat"] / 1000 for u in xs]; mean_lat += lat
        p1, p2 = st.median(l1) if l1 else None, st.median(l2) if l2 else None
        drift = f"{100 * (p2 - p1) / p1:+.0f}%" if p1 and p2 else "–"
        lines.append(f"| {sid} | {len(xs)} | {pct(lat, .5):.0f} | {pct(lat, .95):.0f} | {p1 if p1 is None else round(p1)} | {p2 if p2 is None else round(p2)} | {drift} |")
    if mean_lat:
        little = rate * (st.mean(mean_lat) + 3.0)
        lines += ["", f"Little's law check: {rate:.2f}/s x ({st.mean(mean_lat):.0f} s mean + 3 s think) = {little:.0f} resident, against {users} sessions held and {resident:.0f} measured in flight.",
                  f"Failures in the hold: {sum(1 for u in all_units if not u.get('ok'))} of {len(all_units)}."]
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
