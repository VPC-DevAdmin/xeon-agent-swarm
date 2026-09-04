#!/bin/bash
# Per-archetype cost measurement: each archetype ALONE, at two per-instance
# rates, short plateaus; the slope of busy CPU against rate gives that
# archetype's core-seconds per workflow by component, with the fixed floor
# cancelled. Usage: archetype_costs.sh <seed> [rate_lo rate_hi] [hold_s]
set -uo pipefail
SEED=${1:?seed}; LO=${2:-2}; HI=${3:-4}; HOLD=${4:-240}
R=$HOME/work/repos/xeon-agent-swarm; cd "$R"
OUT=data/capacity/archetypes-$SEED-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"
echo "archetype costs -> $OUT (rates $LO $HI, hold $HOLD)" | tee "$OUT/log"
# Per-archetype rates keep each run below its own knee (a researcher alone
# at 4/s per instance is 48 rerank calls/s, past a 14-core tier), and the
# task agent runs faster so its small cost clears the floor's noise.
# Heavy-mix archetypes run at fractional per-instance rates: alone, the
# code agent (~118 core-s/wf: three Lua+SQLite build-and-test steps) and
# the XL analyst (~94) saturate the box near 0.5 workflows/s box-wide, so
# their two rates sit well under that; give them a longer hold (4th arg).
rates_for() { case "$1" in
  research_brief) echo "1 2";; task_ticket) echo "4 8";;
  code_agent) echo "0.04 0.08";; analyst_xl) echo "0.05 0.1";; deep_research) echo "0.15 0.3";;
  ingestion) echo "0.03 0.06";;   # ~480 chunks per workflow at ~22 chunks/s per embedder core: keep the ingest tier under ~65%
  *) echo "$LO $HI";; esac; }
for sid in ${ARCHETYPES:-task_ticket digest comparison research_brief data_analysis}; do
  echo "=== $sid ($(date +%H:%M:%S))" | tee -a "$OUT/log"
  set -- $(rates_for "$sid")
  FLEET_MIX=custom FLEET_SCENARIOS="[\"$sid\"]" PLATEAU_HOLD=$HOLD scripts/plateau_series.sh "$SEED" "$1" "$2" > "$OUT/$sid.log" 2>&1
  d=$(grep "SERIES DONE" "$OUT/$sid.log" | awk '{print $3}'); echo "$sid $d" >> "$OUT/series.txt"; echo "  -> $d" | tee -a "$OUT/log"
  SEED=$((SEED + 1))
done
PYTHONPATH=. .venv/bin/python scripts/archetype_cost_summary.py "$OUT" 2>&1 | grep -v Warning | tee -a "$OUT/log"
echo "ARCHETYPES DONE $OUT" | tee -a "$OUT/log"
