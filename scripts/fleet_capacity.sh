#!/bin/bash
# Fleet capacity run: K complete orchestrator instances on one box, each with
# its own relay core, executor slice, mock router, database, and evidence
# ledger. This is how a deployment escapes the single-core relay, and the
# fleet's summed capability is the BOX's number, with per-instance integrity
# and post-judgment intact.
#
# Usage: fleet_capacity.sh <instances> <workers-per-instance> <seed>
set -euo pipefail
K=${1:?instances}
W=${2:?workers per instance}
SEED=${3:?seed}
R=$HOME/work/repos/xeon-agent-swarm
cd "$R"
set -a; source .env.adl; set +a
BASE_DB_URL=${DATABASE_URL%/*}      # strip the db name, keep creds/host/port
PG_PORT=$(echo "$DATABASE_URL" | sed -E 's|.*:([0-9]+)/.*|\1|')
PIDS=()

for i in $(seq 1 "$K"); do
  docker exec xeon-pg psql -U xeon -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname='orchestrator_f$i'" | grep -q 1 ||
    docker exec xeon-pg psql -U xeon -d postgres -c \
      "CREATE DATABASE orchestrator_f$i OWNER xeon"
done

for i in $(seq 1 "$K"); do
  PORT=$((8100 + i * 10))
  mkdir -p "data/capacity/fleet$i"
  env PORT=$PORT \
      ADL_WORKERS=$W \
      ADL_WORKER_BASE_PORT=$((9000 + i * 300)) \
      DATABASE_URL="$BASE_DB_URL/orchestrator_f$i" \
      CAPACITY_AGENT_HOST_MOCK_BASE_URL="http://127.0.0.1:$((8920 + i))/v1" \
      CAPACITY_RESULTS_DIR="data/capacity/fleet$i" \
      CAPACITY_FLEET=1 \
      SCHEDULER_ENABLED=0 \
      nohup .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 \
        --port $PORT --log-level warning \
        > "data/capacity/fleet$i/instance.log" 2>&1 &
  PIDS+=($!)
  echo "instance $i: port $PORT pid ${PIDS[-1]}"
done

echo "waiting for $K instances healthy..."
for i in $(seq 1 "$K"); do
  PORT=$((8100 + i * 10))
  for _ in $(seq 1 60); do
    curl -sf "localhost:$PORT/healthz" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -sf "localhost:$PORT/healthz" >/dev/null || { echo "instance $i NOT healthy"; exit 1; }
done
sleep 20   # executor pools

for i in $(seq 1 "$K"); do
  PORT=$((8100 + i * 10))
  curl -s -X POST "localhost:$PORT/capacity/start" -H 'Content-Type: application/json' \
    -d "{\"seed\":$((SEED + i)),\"benchmark_target\":\"agent_host\",\"inference_backend\":\"remote_mock\",\"mix\":\"tile\"}"
  echo " <- instance $i started"
done

echo "fleet running; polling..."
while true; do
  sleep 30
  done_n=0
  line=""
  for i in $(seq 1 "$K"); do
    PORT=$((8100 + i * 10))
    s=$(curl -s "localhost:$PORT/capacity/status" || echo '{}')
    ph=$(python3 -c "import json,sys;d=json.loads(sys.stdin.read() or '{}');print(d.get('phase'),d.get('users'))" <<<"$s")
    line="$line [i$i $ph]"
    case "$ph" in done*|stopped*|error*|idle*) done_n=$((done_n+1));; esac
  done
  echo "FLEET:$line"
  [ "$done_n" -eq "$K" ] && break
done

echo "fleet complete; instance results:"
for i in $(seq 1 "$K"); do
  ls -t "data/capacity/fleet$i"/capacity-*.json 2>/dev/null | head -1
done
echo "stopping instances"
kill "${PIDS[@]}" 2>/dev/null || true
echo "FLEET DONE"
