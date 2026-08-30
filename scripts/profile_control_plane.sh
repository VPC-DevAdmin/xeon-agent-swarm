#!/bin/bash
# Profile the control-plane process through one capacity run.
# Captures py-spy samples (nonblocking, so the event loop is never paused)
# at mid ramp and near the boundary, plus per-thread CPU snapshots.
set -u
R=$HOME/work/repos/xeon-agent-swarm
OUT=$R/data/profiling
mkdir -p "$OUT"
PID=$(systemctl show -p MainPID --value xeon-agents)
echo "control pid $PID"
TOK=$(grep '^CAPACITY_CONTROL_TOKEN' "$R/.env" 2>/dev/null | cut -d= -f2)
curl -s -X POST localhost:8010/capacity/start -H 'Content-Type: application/json' \
  ${TOK:+-H "X-Capacity-Token: $TOK"} \
  -d '{"seed":61329,"benchmark_target":"agent_host","inference_backend":"remote_mock","mix":"tile"}'
echo
pidstat -h -p "$PID" 5 > "$OUT/cp-pidstat.txt" 2>/dev/null &
PIDSTAT_JOB=$!
did_mid=0; did_high=0
while true; do
  S=$(curl -s localhost:8010/capacity/status || echo '{}')
  read -r users active phase <<<"$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read() or '{}')
print(d.get('users') or 0, d.get('active'), d.get('phase'))" <<<"$S")"
  echo "poll users=$users active=$active phase=$phase"
  polls=$((${polls:-0}+1))
  if [ "$active" != "True" ] && [ "$polls" -gt 4 ]; then
    echo "RUN ENDED phase=$phase"; break
  fi
  if [ "$did_mid" = 0 ] && [ "$users" -ge 400 ]; then
    did_mid=1; echo "MID capture at $users users"
    sudo "$R/.venv/bin/py-spy" record --pid "$PID" -d 60 -r 100 --nonblocking \
      --format raw -o "$OUT/cp-mid.raw" 2>&1 | tail -1
    pidstat -h -t -p "$PID" 1 5 > "$OUT/cp-threads-mid.txt" 2>/dev/null
    echo "MID done"
  fi
  if [ "$did_high" = 0 ] && [ "$users" -ge 1000 ]; then
    did_high=1; echo "HIGH capture at $users users"
    sudo "$R/.venv/bin/py-spy" record --pid "$PID" -d 90 -r 100 --nonblocking \
      --format raw -o "$OUT/cp-high.raw" 2>&1 | tail -1
    pidstat -h -t -p "$PID" 1 5 > "$OUT/cp-threads-high.txt" 2>/dev/null
    echo "HIGH done"
  fi
  sleep 15
done
kill "$PIDSTAT_JOB" 2>/dev/null
echo "PROFILING COMPLETE: $(ls -la "$OUT" | tail -6)"
