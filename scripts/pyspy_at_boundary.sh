#!/bin/bash
# One-shot: sample the control plane for 60s once the current run crosses
# 1,100 users, then exit. Companion to profile_control_plane.sh for runs
# started elsewhere (e.g. a repeat set).
set -u
R=$HOME/work/repos/xeon-agent-swarm
OUT=$R/data/profiling
mkdir -p "$OUT"
PID=$(systemctl show -p MainPID --value xeon-agents)
SUFFIX=${1:-sharded}
for i in $(seq 1 720); do
  users=$(curl -s localhost:8010/capacity/status | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('users') or 0)
except Exception: print(0)")
  if [ "$users" -ge 1100 ]; then
    echo "capturing at $users users"
    sudo "$R/.venv/bin/py-spy" record --pid "$PID" -d 60 -r 100 --nonblocking \
      --format raw -o "$OUT/cp-high-$SUFFIX.raw" 2>&1 | tail -1
    pidstat -h -t -p "$PID" 1 5 > "$OUT/cp-threads-$SUFFIX.txt" 2>/dev/null
    echo "CAPTURED at $users users"
    exit 0
  fi
  sleep 10
done
echo "NEVER REACHED 1100 users"
exit 1
