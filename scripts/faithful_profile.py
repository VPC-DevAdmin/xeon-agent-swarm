"""Build the shape-faithful serving profile from a recorded one.

    PYTHONPATH=. .venv/bin/python scripts/faithful_profile.py <profile-dir> <queryset.jsonl> <out-dir>

The workflow expects a tool call or a drafted section at each call
position (the stand-in's own transcript says which). A recorded model does
not always comply: one narrates where it should delegate, another calls a
tool where it should draft. The faithful profile keeps only the recorded
turns whose type matches the position's, so the stand-in answers every
position with the token counts the model produced when it did what the
workflow calls for. Prints tokens per workflow by archetype, weighted by
calls_per_workflow, and which positions had to fall back to the same role
elsewhere.
"""
import json
import os
import statistics as st
import sys

prof, qs, out = sys.argv[1], sys.argv[2], sys.argv[3]
expected, mult = {}, {}
for line in open(qs):
    it = json.loads(line)
    sr = it.get("stand_in_response") or {}
    msg = (sr.get("choices") or [{}])[0].get("message", {}) if isinstance(sr, dict) and sr.get("choices") else sr
    expected.setdefault(it["key"], "tool" if (msg or {}).get("tool_calls") else "text")
    mult[it["key"]] = it.get("calls_per_workflow", 1.0)
rows = [json.loads(line) for line in open(os.path.join(prof, "calls.jsonl"))]
keep, missing = [], set(expected)
for r in rows:
    if not r.get("ok"):
        continue
    did = "tool" if (r.get("message") or {}).get("tool_calls") else "text"
    if did == expected.get(r["key"], did):
        keep.append(r)
        missing.discard(r["key"])
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "calls.jsonl"), "w") as fh:
    for r in keep:
        fh.write(json.dumps(r) + "\n")
levels = sorted({r["concurrency"] for r in keep})
lo = [r for r in keep if r["concurrency"] == levels[0]]
by_role = {}
for r in lo:
    by_role.setdefault((r["role"], expected[r["key"]]), []).append(r["completion_tokens"])
lines = ["| archetype | prompt tokens per workflow | completion tokens per workflow | positions filled from the same role elsewhere |", "|---|---|---|---|"]
for arch in sorted({k.split("/")[0] for k in expected}):
    pin = pout = 0.0
    fb = []
    for k in (k for k in expected if k.startswith(arch + "/")):
        xs = [r for r in lo if r["key"] == k]
        w = float(mult.get(k, 1.0))
        if xs:
            pin += w * st.median(r["prompt_tokens"] for r in xs)
            pout += w * st.median(r["completion_tokens"] for r in xs)
        else:
            role = k.split("/")[1]
            alt = by_role.get((role, expected[k])) or [0]
            pout += w * st.median(alt)
            fb.append(k.split("/", 1)[1])
    lines.append(f"| {arch} | {pin:,.0f} | {pout:,.0f} | {', '.join(fb) or '-'} |")
summary = "\n".join(lines)
open(os.path.join(out, "summary.md"), "w").write(f"# Shape-faithful profile from {prof}\n\nKept {len(keep)} of {sum(1 for r in rows if r.get('ok'))} recorded turns.\n\n{summary}\n")
print(f"kept {len(keep)} of {sum(1 for r in rows if r.get('ok'))} turns; {len(missing)} positions without a faithful turn")
print(summary)
