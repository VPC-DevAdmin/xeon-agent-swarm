#!/usr/bin/env bash
# Validation spike: send a base64-encoded image to the vllm-vision service
# and print the model's response. Used to confirm that vLLM on CPU can
# actually serve a VLM before we build a vision worker against it.
#
# Usage:
#   scripts/test_vlm.sh <image-path> ["prompt text"]
#
# Env overrides:
#   VLM_ENDPOINT  (default: http://localhost:8084)
#   VLM_MODEL     (default: microsoft/Phi-3.5-vision-instruct)
#
# Prereqs:
#   docker compose --profile vision up -d vllm-vision
#   (then wait until the container is "healthy" — can take several minutes
#    on first boot while weights download)

set -euo pipefail

IMAGE_PATH="${1:-}"
PROMPT="${2:-Describe this image in one sentence, then list any numeric data you can see.}"
ENDPOINT="${VLM_ENDPOINT:-http://localhost:8084}"
MODEL="${VLM_MODEL:-microsoft/Phi-3.5-vision-instruct}"

if [[ -z "$IMAGE_PATH" ]]; then
  echo "Usage: $0 <image-path> [prompt]" >&2
  exit 1
fi

if [[ ! -f "$IMAGE_PATH" ]]; then
  echo "File not found: $IMAGE_PATH" >&2
  exit 1
fi

# Quick readiness check so we fail fast with a useful message.
if ! curl -sSf --max-time 3 "$ENDPOINT/v1/models" >/dev/null 2>&1; then
  echo "vllm-vision is not answering on $ENDPOINT/v1/models." >&2
  echo "Start it with:  docker compose --profile vision up -d vllm-vision" >&2
  echo "And wait until it's healthy:  docker compose ps vllm-vision" >&2
  exit 2
fi

# Build the request body in Python to keep base64 and JSON escaping clean.
python3 - "$IMAGE_PATH" "$PROMPT" "$MODEL" "$ENDPOINT" <<'PY'
import base64, json, sys, time, urllib.request, urllib.error

image_path, prompt, model, endpoint = sys.argv[1:5]

with open(image_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

# Best-effort mime inference from extension
ext = image_path.rsplit(".", 1)[-1].lower()
mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")

body = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ],
    "max_tokens": 256,
    "temperature": 0.2,
}

req = urllib.request.Request(
    f"{endpoint}/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)

t0 = time.perf_counter()
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode(errors='replace')}", file=sys.stderr)
    sys.exit(3)
latency_s = time.perf_counter() - t0

msg = data.get("choices", [{}])[0].get("message", {}).get("content", "")
usage = data.get("usage", {})
print("── response ──")
print(msg.strip() or "(empty)")
print()
print("── meta ──")
print(f"latency: {latency_s:.1f}s")
print(f"tokens: in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}")
print(f"model: {data.get('model', model)}")
PY
