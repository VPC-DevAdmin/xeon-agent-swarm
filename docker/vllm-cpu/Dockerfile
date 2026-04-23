FROM python:3.11-slim

LABEL description="vLLM native CPU backend — tensor parallelism, BF16/AMX, chunked prefill"

# ── System dependencies ────────────────────────────────────────────────────────
# numactl/libnuma:         NUMA-aware memory allocation (critical on multi-socket Xeon)
# libtcmalloc-minimal4:   fast malloc — reduces fragmentation under concurrent load
# ca-certificates:        HTTPS for HuggingFace Hub model downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
        numactl \
        libnuma-dev \
        libtcmalloc-minimal4 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── vLLM native CPU wheel ──────────────────────────────────────────────────────
# The native CPU backend (not OpenVINO) supports:
#   --tensor-parallel-size N      (distributes KV cache across N worker processes)
#   --dtype bfloat16              (AMX BF16 TMUL on Sapphire Rapids+)
#   --distributed-executor-backend mp
#   --enable-chunked-prefill
#   VLLM_CPU_OMP_THREADS_BIND    (pin each TP worker to distinct core ranges)
#   VLLM_CPU_KVCACHE_SPACE       (KV cache allocation per worker in GB)
#
# To pin a version: set VLLM_VERSION=x.y.z in .env  →  docker compose build vllm-text
# Leave blank (default) to auto-install the latest GitHub release at build time.
ARG VLLM_VERSION=""

# Copy helper scripts into the image — avoids all shell/Python escaping issues.
COPY get_vllm_version.py /tmp/get_vllm_version.py

RUN pip install --upgrade pip && \
    if [ -z "${VLLM_VERSION}" ]; then \
        VER=$(python3 /tmp/get_vllm_version.py); \
    else \
        VER="${VLLM_VERSION}"; \
    fi && \
    echo "Installing vLLM CPU wheel ${VER}" && \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        "https://github.com/vllm-project/vllm/releases/download/v${VER}/vllm-${VER}+cpu-cp38-abi3-manylinux_2_35_x86_64.whl" && \
    echo "${VER}" > /vllm-version.txt && \
    echo "Installed vLLM $(cat /vllm-version.txt)" && \
    rm -f /tmp/get_vllm_version.py

# ── LD_PRELOAD: tcmalloc + Intel OpenMP ───────────────────────────────────────
# intel-openmp (libiomp5) is pulled in as a wheel dependency of torch[cpu].
# find_preload.py locates both libraries and writes /etc/vllm-preload.env so
# the entrypoint can set LD_PRELOAD without running ldconfig at every start.
COPY find_preload.py /tmp/find_preload.py
RUN python3 /tmp/find_preload.py && rm -f /tmp/find_preload.py

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Sources LD_PRELOAD paths from /etc/vllm-preload.env, then execs the vLLM
# OpenAI-compatible API server.  All flags (--model, --port, --tensor-parallel-size,
# --dtype, etc.) come from docker-compose `command:` and are forwarded via "$@".
RUN printf '#!/bin/sh\n\
. /etc/vllm-preload.env\n\
PRELOAD=""\n\
[ -n "${TCMALLOC_PATH}" ] && PRELOAD="${TCMALLOC_PATH}"\n\
[ -n "${IOMP_PATH}" ] && PRELOAD="${PRELOAD:+${PRELOAD}:}${IOMP_PATH}"\n\
[ -n "${PRELOAD}" ] && export LD_PRELOAD="${PRELOAD}${LD_PRELOAD:+:${LD_PRELOAD}}"\n\
exec python3 -m vllm.entrypoints.openai.api_server "$@"\n' \
    > /usr/local/bin/vllm-cpu-entrypoint && \
    chmod +x /usr/local/bin/vllm-cpu-entrypoint

ENV VLLM_TARGET_DEVICE=cpu

ENTRYPOINT ["/usr/local/bin/vllm-cpu-entrypoint"]
