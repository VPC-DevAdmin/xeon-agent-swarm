"""Capture the workload's representative query set from a traced run.

    PYTHONPATH=. .venv/bin/python scripts/capture_query_set.py <trace-dir> <out.jsonl> [--per-key 5]

A run with CAPACITY_MOCK_TRACE_DIR set writes every model call the
orchestrator made (request messages, tools, the stand-in's answer) to the
trace directory, one file per stand-in worker. This dedupes them by call
position (archetype / role / phase, scripts/mock_router.call_key) keeping
up to --per-key examples from different units, so the set is the whole
workflow shape with a few seeds' worth of content variation. The set is
what scripts/replay_query_set.py sends to a real endpoint.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_dir")
    ap.add_argument("out")
    ap.add_argument("--per-key", type=int, default=5)
    a = ap.parse_args()
    seen: dict[str, list] = collections.defaultdict(list)
    digests: set[str] = set()
    n = 0
    for f in sorted(glob.glob(os.path.join(a.trace_dir, "trace-*.jsonl"))):
        for line in open(f):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            key = r.get("key") or "unknown"
            if len(seen[key]) >= a.per_key:
                continue
            d = hashlib.sha1(json.dumps(r.get("messages"), sort_keys=True).encode()).hexdigest()
            if d in digests:
                continue
            digests.add(d)
            seen[key].append({"key": key, "archetype": key.split("/")[0], "role": key.split("/")[1],
                              "phase": key.split("/")[2], "model": r.get("model"),
                              "messages": r["messages"], "tools": r.get("tools"),
                              "max_tokens": r.get("max_tokens"),
                              "stand_in_response": r.get("response"), "stand_in_usage": r.get("usage")})
    rows = [x for k in sorted(seen) for x in seen[k]]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        for x in rows:
            fh.write(json.dumps(x) + "\n")
    by_arch = collections.Counter(x["archetype"] for x in rows)
    print(f"traced calls: {n}; query set: {len(rows)} calls over {len(seen)} positions -> {a.out}")
    for arch, c in sorted(by_arch.items()):
        keys = sorted({x['key'] for x in rows if x['archetype'] == arch})
        print(f"  {arch}: {c} calls, {len(keys)} positions")


if __name__ == "__main__":
    main()
