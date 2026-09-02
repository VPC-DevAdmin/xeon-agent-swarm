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
    -v "$VOL:/data" "$IMG" --model-id "$model" >/dev/null
  echo "started $name ($model) on :$port"
}

start_tei tei-embed 8880 sentence-transformers/all-MiniLM-L6-v2
start_tei tei-rerank 8881 cross-encoder/ms-marco-MiniLM-L-6-v2

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
