#!/bin/bash
# Provision the v16b retrieval stack on this box, idempotently:
#   - two TEI (text-embeddings-inference) CPU containers, the real models:
#       :8880  embedder   sentence-transformers/all-MiniLM-L6-v2
#       :8881  reranker   cross-encoder/ms-marco-MiniLM-L-6-v2
#   - the seeded corpus store (built once, shared read-only)
# Model weights cache in the tei_data volume, so re-provision is instant.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=ghcr.io/huggingface/text-embeddings-inference:cpu-latest
VOL=xeon-agent-swarm_tei_data

start_tei() {
  local name=$1 port=$2 model=$3
  if docker ps --format '{{.Names}}' | grep -q "^$name$"; then
    return 0
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" -p "127.0.0.1:$port:80" \
    --cpuset-cpus "$4" -e OMP_NUM_THREADS="$5" -e RAYON_NUM_THREADS="$5" \
    -e MKL_NUM_THREADS="$5" \
    -v "$VOL:/data" "$IMG" --model-id "$model" >/dev/null
  echo "started $name ($model) on :$port"
}

# The retrieval tier is SIZED like any deployed service - but by CPUSET,
# not quota: a --cpus quota with 128 visible cores made TEI spawn 128
# threads that burned the quota instantly each scheduling period and then
# slept (throttle-thrash: milliseconds became 30-second ReadTimeouts).
# Pinned cores + matching thread limits give it honest, steady capacity.
start_tei tei-embed 8880 sentence-transformers/all-MiniLM-L6-v2 "120-127" 8
start_tei tei-rerank 8881 cross-encoder/ms-marco-MiniLM-L-6-v2 "88-119" 32
echo "retrieval allocations: embed cpus 120-127, rerank cpus 88-119"

for port in 8880 8881; do
  for _ in $(seq 1 120); do
    curl -sf "localhost:$port/health" >/dev/null 2>&1 && break
    sleep 5
  done
  curl -sf "localhost:$port/health" >/dev/null || { echo "TEI on :$port NOT healthy"; exit 1; }
  echo "TEI :$port healthy"
done

(cd "$R" && .venv/bin/python -c "from backend.capacity import retrieval; print('corpus:', retrieval.ensure_corpus())")
echo "RETRIEVAL PROVISIONED"
