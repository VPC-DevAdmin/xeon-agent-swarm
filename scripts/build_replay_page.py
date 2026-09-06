"""Inline a replay event file into the dashboard page.

    python scripts/build_replay_page.py <events.json> <out.html> [<src.html>]

The source defaults to docs/dashboard/agent-replay.src.html (the ladder
replay); docs/dashboard/steady-state.src.html is the operating-point view.
"""
import sys
src_path = sys.argv[3] if len(sys.argv) > 3 else "docs/dashboard/agent-replay.src.html"
src = open(src_path).read()
data = open(sys.argv[1]).read()
out = src.replace("/*__DATA__*/null", data, 1)
open(sys.argv[2], "w").write(out)
print(sys.argv[2], len(out) // 1024, "KB")
