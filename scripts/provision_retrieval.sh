#!/bin/bash
# Provision the v16 retrieval stack on this box, idempotently:
#   :8880  embedder   TEI container, sentence-transformers/all-MiniLM-L6-v2
#   :8881.. reranker  ONNX Runtime servers (scripts/ort_rerank_server.py),
#                     cross-encoder/ms-marco-MiniLM-L-6-v2 quantized to INT8;
#                     one process per port, each pinned to its own cores
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
# Allocation is by WHOLE PHYSICAL CORE, read from the topology. The box is
# 64 cores x 2 SMT threads with an irregular sibling map (cpu0<->72,
# cpu56<->120), and a cpuset written as a logical range ("56-127") handed
# the reranker 8 whole cores plus 48 half-cores whose siblings ran
# executors. Measured on whole cores (series 7104 and the placement
# benchmarks): the INT8 reranker costs ~1 physical core per 3 rerank
# calls/s (16 pairs, ~125 tokens each) however workers and threads are
# split, and two runtime threads on one core's SMT siblings halve each
# other, so the runtime gets ONE thread per physical core (the first
# sibling of each) and the second siblings stay idle. The rest of the box
# measured ~0.4 logical cpu per workflow/s; with retrieval at 1.33 rerank
# calls per workflow, 40 reranker cores (~90 workflows/s) and 20 cores for
# everything else (~100 workflows/s) meet where the host itself is full.
# v17 rebalance (series 7702): with sandboxed execution in the tile the
# executors' side saturates first (sandbox jobs waited 9-39 s for CPU at
# 8/s per instance while the reranker ran at ~15%). Per tile-weighted
# workflow: ~0.7 core-s of sandbox, ~0.3 of reranking, ~0.25 of
# orchestration, so the tier gets 14 + 2 cores and the rest 48.
# Headroom, not balance (set 8100): sized to saturate with the executors'
# side, the 14-core tier sat at ~90% of its pair budget at 40 workflows/s
# and queued 13-15 s per call. A tier is sized so its utilization at the
# executor-side cliff stays under ~70%: 44 workflows/s x 0.67 calls x 16
# pairs = 470 pairs/s, 35 pairs per core-second -> 20 cores at 67%.
# ...and the executors' side needs its headroom too: at 20 tier cores the
# remaining 42 collapsed at 40 workflows/s (sandbox jobs alone 50% of all
# threads, host 82%). The reference mix needs ~33 cores of sandbox, 10-12
# of orchestration, 12 of reranking at full utilization and 2 of embedding
# at 40 workflows/s - 58-60 of 64 physical cores, which is the box's
# limit. 16 tier cores leave 46: the tier at ~70% and the executors at
# ~87% near 36 workflows/s, both over at 40.
RERANK_PHYS_CORES=${RERANK_PHYS_CORES:-16}
EMBED_PHYS_CORES=${EMBED_PHYS_CORES:-2}
RERANK_WORKERS=${RERANK_WORKERS:-4}
RERANK_THREADS=${RERANK_THREADS:-4}
# The tier's queue is sized FROM the executors' admission gates, never a
# constant: K instances x W executors x CAPACITY_RERANK_CONCURRENCY calls
# may be in flight at once, and a per-worker queue below that share
# refuses calls the cores could serve (each refusal is a 0.25-10 s
# backoff: with 2 workers x 96 slots against 448 admitted, a 0.3 s
# retrieval took 5-7 s at 40 workflows/s and looked like a latency knee).
FLEET_K=${FLEET_K:-4}; FLEET_W=${FLEET_W:-28}; GATE=${CAPACITY_RERANK_CONCURRENCY:-4}
RERANK_MAX_QUEUE=${RERANK_MAX_QUEUE:-$(( (FLEET_K * FLEET_W * GATE + RERANK_WORKERS - 1) / RERANK_WORKERS + 32 ))}
phys_cpuset() {  # $1 = how many physical cores, $2 = skip this many from the top, $3 = first|all siblings
  python3 - "$1" "$2" "$3" <<'PY'
import sys, glob
n, skip, which = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
cores = {}
for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
    cpu = int(p.split("/")[5][3:]); cid = int(open(p).read())
    cores.setdefault(cid, set()).add(cpu)
order = sorted(cores)
chosen = order[len(order) - skip - n: len(order) - skip]
if which == "first":
    print(",".join(str(min(cores[cid])) for cid in chosen))
else:
    print(",".join(str(c) for cid in chosen for c in sorted(cores[cid])))
PY
}
RERANK_CPUS=${RERANK_CPUS:-$(phys_cpuset "$RERANK_PHYS_CORES" 0 first)}
RERANK_CPUS_ALL=$(phys_cpuset "$RERANK_PHYS_CORES" 0 all)
EMBED_CPUS=${EMBED_CPUS:-$(phys_cpuset "$EMBED_PHYS_CORES" "$RERANK_PHYS_CORES" all)}
# Everything else (instances, executors, mocks, databases) gets the
# remaining whole cores, published for the fleet script to pin to, so no
# executor shares a physical core - and its AMX units - with the tier.
REST_CPUS=${REST_CPUS:-$(python3 - "$RERANK_CPUS_ALL" "$EMBED_CPUS" <<'PY'
import sys, os
taken = set()
for arg in sys.argv[1:]:
    for part in arg.split(","):
        if "-" in part:
            a, b = part.split("-"); taken.update(range(int(a), int(b) + 1))
        elif part:
            taken.add(int(part))
ncpu = os.cpu_count() or 128
print(",".join(str(c) for c in range(ncpu) if c not in taken))
PY
)}
# The tier is RERANK_WORKERS independent server PROCESSES on ports 8881..,
# each pinned to RERANK_THREADS whole cores of its own (one inference
# thread per core). It was one uvicorn socket shared by four workers, but
# the kernel hands each keep-alive CONNECTION to a worker, not each
# request: 229 of 269 executor connections sat on one worker, whose queue
# filled at a third of the tier's pair budget while the others idled, and
# which series hit it depended on how connections fell at warm-up. The
# executors rotate across the ports per call (CAPACITY_RERANK_URL is the
# comma-separated list), so the tier's throughput is the sum of its parts.
IFS=, read -ra _RC <<< "$RERANK_CPUS"
if [ "${#_RC[@]}" -ne $((RERANK_WORKERS * RERANK_THREADS)) ]; then
  echo "reranker cores (${#_RC[@]}) must equal workers x threads ($RERANK_WORKERS x $RERANK_THREADS)"; exit 1
fi
RERANK_CPUSETS=(); RERANK_URLS=""
for ((i = 0; i < RERANK_WORKERS; i++)); do
  RERANK_CPUSETS+=("$(IFS=,; echo "${_RC[*]:i*RERANK_THREADS:RERANK_THREADS}")")
  RERANK_URLS="${RERANK_URLS:+$RERANK_URLS,}http://127.0.0.1:$((8881 + i))"
done
mkdir -p "$R/data/capacity/retrieval"
printf 'RERANK_CPUS=%s\nEMBED_CPUS=%s\nREST_CPUS=%s\nRERANK_WORKERS=%s\nRERANK_THREADS=%s\nRERANK_MAX_QUEUE=%s\nRERANK_URLS=%s\nRERANK_CPUSETS=%s\n' \
  "$RERANK_CPUS" "$EMBED_CPUS" "$REST_CPUS" "$RERANK_WORKERS" "$RERANK_THREADS" "$RERANK_MAX_QUEUE" \
  "$RERANK_URLS" "$(IFS=';'; echo "${RERANK_CPUSETS[*]}")" \
  > "$R/data/capacity/retrieval/allocation.env"

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

rerank_server_ok() {  # $1 = index: healthy on its port, on its cpuset, with the sized queue
  local i=$1 port=$((8881 + $1)) pid have want q
  curl -sf "localhost:$port/health" 2>/dev/null | grep -q '"model"' || return 1
  pid=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oE "pid=[0-9]+" | head -1 | cut -d= -f2)
  [ -n "$pid" ] || return 1
  have=$(taskset -cp "$pid" 2>/dev/null | awk -F': ' '{print $2}')
  # taskset prints a normalized list ("56-127"); normalize the wanted one
  # the same way via a throwaway process under that affinity.
  want=$(taskset -c "${RERANK_CPUSETS[$i]}" bash -c 'taskset -cp $$' 2>/dev/null | awk -F': ' '{print $2}')
  q=$(curl -sf "localhost:$port/health" 2>/dev/null | grep -oE '"max_queue":[0-9]+' | cut -d: -f2)
  [ "$have" = "$want" ] && [ "${q:-0}" -eq "$RERANK_MAX_QUEUE" ]
}

start_ort_reranker() {
  # Already the ORT servers (their /health carries the model path), one per
  # port on the same allocation? Keep them. Any change restarts them all.
  local ok=1 i
  for ((i = 0; i < RERANK_WORKERS; i++)); do
    rerank_server_ok "$i" || ok=0
  done
  if [ "$ok" -eq 1 ] && ! ss -tln 2>/dev/null | grep -q ":$((8881 + RERANK_WORKERS)) "; then
    return 0
  fi
  echo "ort-rerank allocation changed -> restarting ${RERANK_WORKERS} servers"
  docker rm -f tei-rerank >/dev/null 2>&1 || true
  for ((i = 0; i < RERANK_WORKERS + 8; i++)); do
    for p in $(ss -tlnp 2>/dev/null | grep ":$((8881 + i)) " | grep -oE "pid=[0-9]+" | cut -d= -f2 | sort -u); do
      kill -9 "$p" 2>/dev/null || true
    done
  done
  for p in $(pgrep -f "uvicorn scripts.ort_rerank_server:app" || true); do kill -9 "$p" 2>/dev/null || true; done
  sleep 1
  mkdir -p "$R/data/capacity/retrieval"
  # Serial tokenization: the tokenizers crate's rayon workers keep spinning
  # after a parallel encode and steal the very cores the inference threads
  # are about to use (22 ms per batch became 90 ms). Sixteen pairs
  # tokenize in 3 ms on one thread.
  # setsid -f: fully detached (own session, reparented to init) so this
  # script - and the fleet script that calls it - returns immediately.
  for ((i = 0; i < RERANK_WORKERS; i++)); do
    (cd "$R" && TOKENIZERS_PARALLELISM=false RERANK_SPIN=1 \
        RERANK_THREADS="$RERANK_THREADS" RERANK_MAX_QUEUE="$RERANK_MAX_QUEUE" \
        RERANK_MODEL_DIR="$INT8_DIR" \
        setsid -f taskset -c "${RERANK_CPUSETS[$i]}" .venv/bin/uvicorn scripts.ort_rerank_server:app \
          --workers 1 --port $((8881 + i)) --host 127.0.0.1 --log-level warning \
          > "data/capacity/retrieval/ort-rerank-$((8881 + i)).log" 2>&1 < /dev/null)
    echo "started ort-rerank ($INT8_DIR) on :$((8881 + i)), cpus ${RERANK_CPUSETS[$i]}, ${RERANK_THREADS} threads"
  done
}

build_int8_reranker
start_ort_reranker
echo "retrieval allocations: embed cpus $EMBED_CPUS, rerank cpus $RERANK_CPUS (${RERANK_WORKERS} servers x ${RERANK_THREADS} threads: $(IFS=';'; echo "${RERANK_CPUSETS[*]}"))"

for port in 8880 $(seq 8881 $((8880 + RERANK_WORKERS))); do
  for _ in $(seq 1 120); do
    curl -sf "localhost:$port/health" >/dev/null 2>&1 && break
    sleep 5
  done
  curl -sf "localhost:$port/health" >/dev/null || { echo "retrieval service on :$port NOT healthy"; exit 1; }
  echo ":$port healthy"
done

(cd "$R" && .venv/bin/python -c "from backend.capacity import retrieval; print('corpus:', retrieval.ensure_corpus())")
# The ingestion agent's document set (CPU-heavy mix): generated once,
# seeded, not committed (data/capacity/ingest is ignored).
if [ ! -f "$R/data/capacity/ingest/doc-039.pdf" ]; then
  (cd "$R" && PYTHONPATH=. .venv/bin/python scripts/make_ingest_docs.py --docs 40 --pages 20)
fi
(cd "$R" && .venv/bin/python -c "import pypdf" 2>/dev/null) || echo "WARNING: pypdf missing in the venv (pip install pypdf) - the ingestion agent cannot parse"
echo "RETRIEVAL PROVISIONED"
