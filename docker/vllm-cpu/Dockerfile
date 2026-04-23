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
# To pin a version: VLLM_VERSION=x.y.z in .env  →  docker compose build vllm-text
# Leave blank (default) to auto-install the latest GitHub release at build time.
ARG VLLM_VERSION=""

# Resolve version, then install.
# NOTE: no heredocs inside $() — Docker's parser rejects that syntax.
# Instead we write the resolver to a temp script file first.
RUN pip install --upgrade pip && \
    printf 'import json,urllib.request\nwith urllib.request.urlopen("https://api.github.com/repos/vllm-project/vllm/releases/latest",timeout=30) as r:\n    print(json.load(r)["tag_name"].lstrip("v"))\n' \
        > /tmp/get_vllm_ver.py && \
    VER="${VLLM_VERSION}" && \
    [ -z "${VER}" ] && VER=$(python3 /tmp/get_vllm_ver.py); \
    rm -f /tmp/get_vllm_ver.py && \
    echo "Installing vLLM CPU wheel ${VER}" && \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        "https://github.com/vllm-project/vllm/releases/download/v${VER}/vllm-${VER}+cpu-cp38-abi3-manylinux_2_35_x86_64.whl" && \
    echo "${VER}" > /vllm-version.txt && \
    echo "Installed vLLM $(cat /vllm-version.txt)"

# ── LD_PRELOAD: tcmalloc + Intel OpenMP ───────────────────────────────────────
# intel-openmp (libiomp5) is pulled in as a wheel dependency.
# Write a helper script, run it, then remove it — avoids heredoc-in-RUN issues.
RUN printf '\
import subprocess, re, glob\n\
\n\
def find_lib(*names):\n\
    try:\n\
        out = subprocess.check_output(["ldconfig", "-p"], text=True)\n\
        for name in names:\n\
            m = re.search(rf"^\\s+{name}\\s.*=>\\s+(\\S+)", out, re.M)\n\
            if m:\n\
                return m.group(1)\n\
    except Exception:\n\
        pass\n\
    for name in names:\n\
        for p in (glob.glob(f"/usr/lib/**/{name}", recursive=True) +\n\
                  glob.glob(f"/usr/local/lib/**/{name}", recursive=True) +\n\
                  glob.glob(f"/usr/lib/x86_64-linux-gnu/{name}")):\n\
            return p\n\
    return ""\n\
\n\
tcmalloc = find_lib("libtcmalloc_minimal.so.4", "libtcmalloc_minimal.so")\n\
iomp     = find_lib("libiomp5.so")\n\
\n\
with open("/etc/vllm-preload.env", "w") as f:\n\
    f.write(f"TCMALLOC_PATH={tcmalloc}\\n")\n\
    f.write(f"IOMP_PATH={iomp}\\n")\n\
\n\
print(f"tcmalloc: {tcmalloc or \"(not found)\"}")\n\
print(f"iomp:     {iomp or \"(not found)\"}")\n\
' > /tmp/find_preload.py && \
    python3 /tmp/find_preload.py && \
    rm -f /tmp/find_preload.py

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Sources LD_PRELOAD paths, then execs the vLLM OpenAI-compatible API server.
# All flags (--model, --port, --tensor-parallel-size, etc.) come from CMD /
# docker-compose `command:` and are forwarded via "$@".
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
