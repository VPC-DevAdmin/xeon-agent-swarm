#!/bin/bash
# Provision the v16 retrieval stack on this box, idempotently:
#   :8880  embedder   TEI container, sentence-transformers/all-MiniLM-L6-v2
#   :8881  reranker   ONNX Runtime server (scripts/ort_rerank_server.py),
#                     cross-encoder/ms-marco-MiniLM-L-6-v2 quantized to INT8
#   the seeded corpus store (built once, shared read-only)
# Model weights cache in the tei_data volume; the INT8 reranker is built
# once into data/local-models/, so re-provision is instant.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
IMG=ghcr.io/huggingface/text-embeddings-inference:cpu-latest
VOL=xeon-agent-swarm_tei_data
RERANK_SRC=cross-encoder/ms-marco-MiniLM-L-6-v2
MODEL_ROOT=${XEON_MODEL_ROOT:-$R/data/local-models}
INT8_DIR=$MODEL_ROOT/ms-marco-int8
# Allocation, v16.1 series 7102 -> 7103: at 32 cores the reranker pin ran
# flat out (26.5% of host = 34 cores of retrieval) at 32 workflows/s with
# the HOST at 45%, so the allocation, not the box, set the knee. Retrieval
# costs ~1.06 cores per workflow/s and the rest of the box ~0.75, so 72
# reranker cores + 8 embedder cores balance against the remaining 48 near
# 60-65 workflows/s, where the host itself saturates.
RERANK_CPUS=${RERANK_CPUS:-56-127}
RERANK_WORKERS=${RERANK_WORKERS:-9}
RERANK_THREADS=${RERANK_THREADS:-8}
EMBED_CPUS=${EMBED_CPUS:-48-55}

start_tei() {
  local name=$1 port=$2 model=$3
  if docker ps --format '{{.Names}}' | grep -q "^$name$"; then
    # Same allocation? Keep it. A changed cpuset is a new configuration.
    if [ "$(docker inspect -f '{{.HostConfig.CpusetCpus}}' "$name" 2>/dev/null)" = "$4" ]; then
      return 0
    fi
    echo "$name cpuset changed -> recreating"
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
start_tei tei-embed 8880 sentence-transformers/all-MiniLM-L6-v2 "$EMBED_CPUS" 8

# Reranker: INT8 on ONNX Runtime, not the TEI container. The quantized
# graph runs 2.6x faster than FP32 under onnxruntime 1.29 (AMX/VNNI int8
# GEMMs) but SLOWER than FP32 under the runtime bundled in TEI's image, so
# the model is served through ORT directly: 1,544 pairs/s at saturation on
# the same 32-core pin where the TEI FP32 reranker ceilinged at 530.
build_int8_reranker() {
  [ -f "$INT8_DIR/onnx/model.onnx" ] && return 0
  echo "building INT8 reranker in $INT8_DIR"
  mkdir -p "$INT8_DIR/onnx"
  # Source files come from the TEI volume's HF snapshot (present after any
  # TEI reranker start); fall back to a direct hub download.
  if ! docker run --rm -v "$VOL:/data" -v "$INT8_DIR:/out" alpine sh -c '
      snap=$(ls -d /data/models--cross-encoder--ms-marco-MiniLM-L-6-v2/snapshots/*/ 2>/dev/null | head -1)
      [ -n "$snap" ] || exit 1
      cp "$snap"/config.json "$snap"/tokenizer.json "$snap"/tokenizer_config.json /out/ &&
      cp "$snap"/onnx/model.onnx /out/onnx/model_fp32.onnx && chmod -R a+rw /out' 2>/dev/null; then
    (cd "$R" && .venv/bin/python - "$RERANK_SRC" "$INT8_DIR" <<'EOF'
import shutil, sys
from huggingface_hub import hf_hub_download
repo, out = sys.argv[1], sys.argv[2]
for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
    shutil.copy(hf_hub_download(repo, f), f"{out}/{f}")
shutil.copy(hf_hub_download(repo, "onnx/model.onnx"), f"{out}/onnx/model_fp32.onnx")
EOF
    )
  fi
  (cd "$R" && .venv/bin/python - "$INT8_DIR" <<'EOF'
import sys
from onnxruntime.quantization import quantize_dynamic, QuantType
d = sys.argv[1]
quantize_dynamic(f"{d}/onnx/model_fp32.onnx", f"{d}/onnx/model.onnx",
                 weight_type=QuantType.QInt8)
print("quantized:", f"{d}/onnx/model.onnx")
EOF
  )
}

start_ort_reranker() {
  # Already the ORT server (its /health carries the model path) on the
  # same allocation? Keep it. A changed cpuset or worker count restarts it.
  if curl -sf localhost:8881/health 2>/dev/null | grep -q '"model"'; then
    local master
    master=$(pgrep -f "uvicorn scripts.ort_rerank_server:app" | head -1 || true)
    if [ -n "$master" ]; then
      local have want
      have=$(taskset -cp "$master" 2>/dev/null | awk -F': ' '{print $2}')
      # taskset prints a normalized list ("56-127"); normalize the wanted
      # one the same way via a throwaway process under that affinity.
      want=$(taskset -c "$RERANK_CPUS" bash -c 'taskset -cp $$' 2>/dev/null | awk -F': ' '{print $2}')
      local nworkers
      nworkers=$(ps --ppid "$master" -o pid= | wc -l)   # workers + spawn tracker
      if [ "$have" = "$want" ] && [ "$nworkers" -eq $((RERANK_WORKERS + 1)) ]; then
        return 0
      fi
      echo "ort-rerank allocation changed ($have x$((nworkers - 1)) -> $want x$RERANK_WORKERS) -> restarting"
    fi
  fi
  docker rm -f tei-rerank >/dev/null 2>&1 || true
  for p in $(ss -tlnp 2>/dev/null | grep ":8881 " | grep -oE "pid=[0-9]+" | cut -d= -f2 | sort -u); do
    kill -9 "$p" 2>/dev/null || true
  done
  sleep 1
  mkdir -p "$R/data/capacity/retrieval"
  # Serial tokenization: the tokenizers crate's rayon workers keep spinning
  # after a parallel encode and steal the very cores the inference threads
  # are about to use (22 ms per batch became 90 ms). Sixteen pairs
  # tokenize in 3 ms on one thread.
  # setsid -f: fully detached (own session, reparented to init) so this
  # script - and the fleet script that calls it - returns immediately.
  (cd "$R" && TOKENIZERS_PARALLELISM=false RERANK_SPIN=1 \
      RERANK_THREADS="$RERANK_THREADS" RERANK_MAX_QUEUE=${RERANK_MAX_QUEUE:-96} \
      RERANK_MODEL_DIR="$INT8_DIR" \
      setsid -f taskset -c "$RERANK_CPUS" .venv/bin/uvicorn scripts.ort_rerank_server:app \
        --workers "$RERANK_WORKERS" --port 8881 --host 127.0.0.1 --log-level warning \
        > data/capacity/retrieval/ort-rerank.log 2>&1 < /dev/null)
  echo "started ort-rerank ($INT8_DIR) on :8881, cpus $RERANK_CPUS, ${RERANK_WORKERS}x${RERANK_THREADS} threads"
}

build_int8_reranker
start_ort_reranker
echo "retrieval allocations: embed cpus $EMBED_CPUS, rerank cpus $RERANK_CPUS (${RERANK_WORKERS}x${RERANK_THREADS})"

for port in 8880 8881; do
  for _ in $(seq 1 120); do
    curl -sf "localhost:$port/health" >/dev/null 2>&1 && break
    sleep 5
  done
  curl -sf "localhost:$port/health" >/dev/null || { echo "retrieval service on :$port NOT healthy"; exit 1; }
  echo ":$port healthy"
done

(cd "$R" && .venv/bin/python -c "from backend.capacity import retrieval; print('corpus:', retrieval.ensure_corpus())")
echo "RETRIEVAL PROVISIONED"
