#!/bin/bash
# The residency photograph: hold a fixed number of agents resident on the
# fleet (closed loop: each session submits a workflow, waits for it, thinks
# 3 s, submits the next) for PLATEAU_HOLD seconds and measure what the
# server sustains. The confirmation run for a certified open-loop point:
# N is the Little's-law residency at that point, and the photograph shows
# the throughput and latencies the server holds with N agents on it.
#
# Usage: residency_photo.sh <seed> <users-per-instance> [hold-s]
# Env:   K (instances, 4), W (executors, 28), CAPACITY_E2E_TILE (tile whose
#        composition the sessions follow, default enterprise)
set -uo pipefail
SEED=${1:?seed}; USERS=${2:?users per instance}; HOLD=${3:-600}
R=$HOME/work/repos/xeon-agent-swarm
cd "$R"
export CAPACITY_MODEL_TTFT_MS=${CAPACITY_MODEL_TTFT_MS:-500}
export CAPACITY_MODEL_DECODE_TPS=${CAPACITY_MODEL_DECODE_TPS:-100}
export CAPACITY_MODEL_PREFILL_TPS=${CAPACITY_MODEL_PREFILL_TPS:-8000}
K=${K:-4}; W=${W:-28}
# Sessions follow the tile's composition, interleaved so any prefix keeps
# the proportions: the enterprise tile is 6 task, 2 code, 2 analyst, 1
# research, 1 ingestion in twelve.
SCEN=${FLEET_SCENARIOS:-'["task_ticket","code_agent","task_ticket","analyst_large","task_ticket","deep_research","task_ticket","ingestion","task_ticket","code_agent","task_ticket","analyst_large"]'}
STAMP=$(date +%Y%m%d-%H%M%S)
OUT=data/capacity/photo-$SEED-$STAMP
mkdir -p "$OUT"
echo "residency photograph seed=$SEED users=$USERS/instance ($((USERS * K)) resident) hold=$HOLD K=$K W=$W -> $OUT"
RESIDENT_USERS=$USERS PLATEAU_HOLD=$HOLD FLEET_MIX=custom FLEET_SCENARIOS="$SCEN" \
  scripts/fleet_capacity.sh "$K" "$W" "$SEED" closed > "$OUT/fleet.log" 2>&1
sleep 8
for i in $(seq 1 "$K"); do
  f=$(ls -t "data/capacity/fleet$i"/capacity-*.json 2>/dev/null | head -1)
  e=$(ls -t "data/capacity/fleet$i"/evidence-*.jsonl.gz 2>/dev/null | head -1)
  [ -n "$f" ] && cp "$f" "$OUT/i$i-$(basename "$f")"
  [ -n "$e" ] && cp "$e" "$OUT/i$i-$(basename "$e")"
done
PYTHONPATH=. .venv/bin/python scripts/residency_summary.py "$OUT" | tee "$OUT/summary.md"
echo "PHOTO DONE $OUT"
