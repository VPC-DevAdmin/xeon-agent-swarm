#!/usr/bin/env bash
# Idempotent bring-up for the local capacity-test LLM: Qwen3-30B-A3B FP8 on
# SGLang/Xeon. Mirrors the known-good run_09 config (launch-qwen3-intel-fp8.sh).
#
# --enable-metrics is added on top of the known-good run_09 flags so the
# capacity tab can read KV-cache utilization from SGLang's /metrics.
# Called by the backend's POST /capacity/engine/start; every echo line streams
# into the UI, so narrate each phase. Exit 0 only when /v1/models answers.
#
# Auto-install behavior:
#   * model missing  -> download Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 from HF
#                       (tens of GB — this phase can take a long time)
#   * image missing  -> fall back to the Docker Hub xeon image WITH A WARNING:
#                       the locally built sglang-cpu:xeon-fixed has AMX-capable
#                       PyTorch; the Hub image can fall back to AVX512 and crash
#                       the FP8 GEMM warmup. Build the local image for real runs.
set -u

PORT="${PORT:-8000}"
CONTAINER_NAME="${CONTAINER_NAME:-sglang-qwen3-intel}"
MODELS_DIR="${MODELS_DIR:-/data/ml/models}"
MODEL_DIR_NAME="${MODEL_DIR_NAME:-Qwen3-30B-A3B-Instruct-2507-FP8}"
MODEL_PATH="/models/${MODEL_DIR_NAME}"
HOST_MODEL_DIR="${MODELS_DIR}/${MODEL_DIR_NAME}"
HF_REPO="${HF_REPO:-Qwen/Qwen3-30B-A3B-Instruct-2507-FP8}"
SERVED_NAME="${SERVED_NAME:-qwen3_30b_a3b}"
IMAGE_LOCAL="sglang-cpu:xeon-fixed"
IMAGE_FALLBACK="lmsysorg/sglang:v0.5.5.post3-xeon"
VENV_BIN="${VENV_BIN:-$(cd "$(dirname "$0")/.." && pwd)/.venv/bin}"

say() { echo "[ensure-llm] $*"; }

# 0. Already serving?
if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
  say "engine already serving on :${PORT} — nothing to do"
  exit 0
fi

command -v docker >/dev/null 2>&1 || { say "ERROR: docker not installed"; exit 1; }

# 1. Container running but not ready yet? Just wait for it below.
if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then

  # 2. Model present? Download if not.
  if [ ! -d "${HOST_MODEL_DIR}" ] || [ -z "$(ls -A "${HOST_MODEL_DIR}" 2>/dev/null)" ]; then
    say "model not found at ${HOST_MODEL_DIR} — downloading ${HF_REPO} (this is tens of GB)"
    mkdir -p "${HOST_MODEL_DIR}"
    if [ ! -x "${VENV_BIN}/huggingface-cli" ]; then
      say "installing huggingface_hub CLI into the app venv"
      "${VENV_BIN}/pip" install -q "huggingface_hub[cli]" || { say "ERROR: pip install huggingface_hub failed"; exit 1; }
    fi
    HF_HOME="${HF_HOME:-/data/ml/huggingface}" \
      "${VENV_BIN}/huggingface-cli" download "${HF_REPO}" \
      --local-dir "${HOST_MODEL_DIR}" 2>&1 | tail -20 \
      || { say "ERROR: model download failed"; exit 1; }
    say "model download complete"
  else
    say "model present at ${HOST_MODEL_DIR}"
  fi

  # 3. Image present? Prefer the local AMX-fixed build; fall back with a warning.
  IMAGE="${IMAGE_LOCAL}"
  if ! docker image inspect "${IMAGE_LOCAL}" >/dev/null 2>&1; then
    say "WARNING: ${IMAGE_LOCAL} not found — falling back to ${IMAGE_FALLBACK}"
    say "WARNING: the Hub image may lack AMX PyTorch and crash FP8 warmup; build ${IMAGE_LOCAL} for reliable runs"
    docker image inspect "${IMAGE_FALLBACK}" >/dev/null 2>&1 \
      || docker pull "${IMAGE_FALLBACK}" || { say "ERROR: docker pull failed"; exit 1; }
    IMAGE="${IMAGE_FALLBACK}"
  else
    say "image ${IMAGE_LOCAL} present"
  fi

  # 4. Launch — flags mirror the known-good run_09 config exactly.
  say "launching ${CONTAINER_NAME} (${IMAGE}) on :${PORT}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker run -d --rm \
      --network host --shm-size=32g \
      --cpuset-cpus 0-63 \
      -v "${MODELS_DIR}:/models" \
      -v /data/ml/huggingface:/root/.cache/huggingface \
      --security-opt seccomp=unconfined --cap-add SYS_NICE \
      --name "${CONTAINER_NAME}" \
      "${IMAGE}" \
      /opt/.venv/bin/python3 -m sglang.launch_server \
          --model-path "${MODEL_PATH}" \
          --served-model-name "${SERVED_NAME}" \
          --device cpu \
          --quantization fp8 \
          --attention-backend intel_amx \
          --max-running-requests 64 \
          --context-length 8192 \
          --chunked-prefill-size 4096 \
          --max-total-tokens 16384 \
          --mem-fraction-static 0.85 \
          --disable-overlap-schedule \
          --enable-metrics \
          --trust-remote-code \
          --host 0.0.0.0 --port "${PORT}" \
      || { say "ERROR: docker run failed"; exit 1; }
else
  say "container ${CONTAINER_NAME} already running — waiting for ready"
fi

# 5. Wait for ready (model load on CPU takes minutes).
say "waiting for engine ready (up to 10 min)"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    say "engine READY after ~$((i*10))s — serving $(curl -s http://127.0.0.1:${PORT}/v1/models | grep -o '"id":"[^"]*"' | head -1)"
    exit 0
  fi
  say "  … still loading ($((i*10))s)"
  sleep 10
done

say "ERROR: timed out; last container log lines:"
docker logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/[sglang] /'
exit 1
