#!/bin/bash
# One plateau series: the fleet held at each per-instance arrival rate in
# turn, one fleet run per rate, so every cohort completes under the rate
# that admitted it. The latency-versus-rate curve and the sustainable rate
# are post-processed from the ledgers (backend.capacity.judge --sweep);
# this script only produces the evidence.
#
# Usage: plateau_series.sh <seed> [rates...]     (default rates 2 4 6 8 12)
# Env:   PLATEAU_HOLD (default 600), K (instances, 4), W (executors, 28),
#        CAPACITY_MODEL_* serving-tier parameters (defaults: the v15/v16
#        realistic tier: TTFT 500 ms, 100 tok/s decode, 8000 tok/s prefill).
set -uo pipefail
SEED=${1:?seed}; shift
RATES=("$@"); [ ${#RATES[@]} -eq 0 ] && RATES=(2 4 6 8 12)
R=$HOME/work/repos/xeon-agent-swarm
cd "$R"
export CAPACITY_MODEL_TTFT_MS=${CAPACITY_MODEL_TTFT_MS:-500}
export CAPACITY_MODEL_DECODE_TPS=${CAPACITY_MODEL_DECODE_TPS:-100}
export CAPACITY_MODEL_PREFILL_TPS=${CAPACITY_MODEL_PREFILL_TPS:-8000}
export PLATEAU_HOLD=${PLATEAU_HOLD:-600}
K=${K:-4}; W=${W:-28}
STAMP=$(date +%Y%m%d-%H%M%S)
OUT=data/capacity/series-$SEED-$STAMP
mkdir -p "$OUT"
echo "series seed=$SEED rates=${RATES[*]} hold=$PLATEAU_HOLD K=$K W=$W -> $OUT"
for rate in "${RATES[@]}"; do
  echo "=== rate $rate/s per instance ($(date +%H:%M:%S))"
  PLATEAU_RATE=$rate scripts/fleet_capacity.sh "$K" "$W" "$SEED" open > "$OUT/rate-$rate.log" 2>&1
  sleep 8   # instances flush and close their ledgers after the stop signal
  for i in $(seq 1 "$K"); do
    f=$(ls -t "data/capacity/fleet$i"/capacity-*.json 2>/dev/null | head -1)
    e=$(ls -t "data/capacity/fleet$i"/evidence-*.jsonl.gz 2>/dev/null | head -1)
    [ -n "$f" ] && cp "$f" "$OUT/rate-$rate-i$i-$(basename "$f")"
    [ -n "$e" ] && cp "$e" "$OUT/rate-$rate-i$i-$(basename "$e")"
    echo "  i$i: $(basename "${f:-none}")"
  done
  sleep 20   # let the last stragglers and the retrieval tier settle
done
echo "SERIES DONE $OUT"
