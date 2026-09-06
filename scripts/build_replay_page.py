"""Inline a replay event file into the dashboard page.

    python scripts/build_replay_page.py data/capacity/replay/enterprise-9801.json docs/dashboard/agent-replay.html
"""
import sys
src = open("docs/dashboard/agent-replay.src.html").read()
data = open(sys.argv[1]).read()
out = src.replace("/*__DATA__*/null", data, 1)
open(sys.argv[2], "w").write(out)
print(sys.argv[2], len(out) // 1024, "KB")
