"""Tokens per workflow by archetype from a recorded serving profile.

    PYTHONPATH=. .venv/bin/python scripts/serving_tokens.py data/capacity/serving/<profile> data/capacity/queryset/<set>.jsonl

Re-reads <profile>/calls.jsonl and weights each call position's median
prompt and completion tokens by calls_per_workflow from the query set.
"""
import json
import sys

sys.path.insert(0, "scripts")
from replay_query_set import per_workflow  # noqa: E402

prof, qs = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(f"{prof}/calls.jsonl")]
mult = {}
for l in open(qs):
    it = json.loads(l)
    mult[it["key"]] = it.get("calls_per_workflow", 1.0)
levels = sorted({r["concurrency"] for r in rows})
print(per_workflow(rows, levels[0], mult))
