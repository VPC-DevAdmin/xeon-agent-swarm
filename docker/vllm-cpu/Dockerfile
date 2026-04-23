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
# To pin a version: docker build --build-arg VLLM_VERSION=0.9.1 .
# Leave blank to install the latest GitHub release automatically.
ARG VLLM_VERSION=""

RUN pip install --upgrade pip && \
    if [ -z "${VLLM_VERSION}" ]; then \
      VLLM_VERSION=$(python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("https://api.github.com/repos/vllm-project/vllm/releases/latest", timeout=30) as r:
    print(json.load(r)["tag_name"].lstrip("v"))
PY
      ); \
    fi && \
    echo "Installing vLLM CPU wheel version ${VLLM_VERSION}" && \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu \
      "https://github.com/vllm-project/vllm/releases/download/v${VLLM_VERSION}/vllm-${VLLM_VERSION}+cpu-cp38-abi3-manylinux_2_35_x86_64.whl" && \
    echo "${VLLM_VERSION}" > /vllm-version.txt && \
    echo "Installed vLLM $(cat /vllm-version.txt)"

# ── LD_PRELOAD: tcmalloc + Intel OpenMP ───────────────────────────────────────
# intel-openmp (libiomp5) is installed as a wheel dependency.
# Bake both library paths into an env file so the entrypoint can set LD_PRELOAD
# without running ldconfig at every container start.
RUN python3 - <<'PY'
import subprocess, re, sys

def find_lib(*names):
    try:
        out = subprocess.check_output(["ldconfig", "-p"], text=True)
        for name in names:
            m = re.search(rf'^\s+{name}\s.*=>\s+(\S+)', out, re.M)
            if m:
                return m.group(1)
    except Exception:
        pass
    # Fallback: search common paths
    import glob
    for name in names:
        for path in glob.glob(f"/usr/lib/**/{name}", recursive=True) + \
                    glob.glob(f"/usr/local/lib/**/{name}", recursive=True) + \
                    glob.glob(f"/usr/lib/x86_64-linux-gnu/{name}"):
            return path
    return ""

tcmalloc = find_lib("libtcmalloc_minimal.so.4", "libtcmalloc_minimal.so", "libtcmalloc.so.4")
iomp     = find_lib("libiomp5.so")

with open("/etc/vllm-preload.env", "w") as f:
    f.write(f"TCMALLOC_PATH={tcmalloc}\n")
    f.write(f"IOMP_PATH={iomp}\n")

print(f"tcmalloc: {tcmalloc or '(not found — install libtcmalloc-minimal4)'}")
print(f"iomp:     {iomp or '(not found — intel-openmp may not have installed correctly)'}")
PY

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Sets LD_PRELOAD (tcmalloc + iomp) then execs the vLLM OpenAI-compatible server.
# All vllm server flags are passed through as CMD / docker-compose `command:`.
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
