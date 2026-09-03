#!/bin/bash
# Direct probe of the retrieval tier while a run is on: every 5 s, time one
# /embed and one /rerank call from outside the executors (no admission
# gate), so tier latency and executor-side queueing can be told apart.
# Usage: tier_probe.sh <out.log> [seconds]
OUT=${1:?out}; DUR=${2:-600}
END=$(( $(date +%s) + DUR ))
TEXTS=$(python3 -c 'import json; print(json.dumps([("Record covers throughput cache scheduler operations. " * 12)] * 16))')
while [ "$(date +%s)" -lt "$END" ]; do
  e=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" -H 'Content-Type: application/json' -d '{"inputs":["topic42 throughput cache"]}' localhost:8880/embed)
  r=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" -H 'Content-Type: application/json' -d "{\"query\":\"topic42 throughput cache\",\"texts\":$TEXTS,\"truncate\":true}" localhost:8881/rerank)
  echo "$(date +%s) embed $e rerank $r load $(cut -d' ' -f1 /proc/loadavg)" >> "$OUT"
  sleep 5
done
