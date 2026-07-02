#!/usr/bin/env bash
#
# Launch the demo stack and hold the terminal until Ctrl-C:
#   [mock router] → backend API → frontend dev server
#
# Invoked by `make demo` (mock router) and `make demo-live` (real router).
# All knobs come in as environment variables (the Makefile sets them):
#   MODE=mock|live  BIN=.venv/bin  BACKEND_PORT  FRONTEND_PORT  MOCK_PORT  ROUTER_BASE
#
set -uo pipefail

MODE="${1:-mock}"
BIN="${BIN:-.venv/bin}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
MOCK_PORT="${MOCK_PORT:-8901}"
ROUTER_BASE="${ROUTER_BASE:-http://localhost:8900}"

mkdir -p data
PIDS=()
cleanup() {
  echo
  echo "→ stopping stack"
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

wait_up() { # url  name  logfile
  for _ in $(seq 1 80); do
    curl -sf "$1" >/dev/null 2>&1 && return 0
    sleep 0.3
  done
  echo "✗ $2 did not come up — last log lines:"
  tail -n 25 "$3" 2>/dev/null
  exit 1
}

if [ "$MODE" = "live" ]; then
  # Real router: load gateway creds first, then honor whatever ROUTER_BASE resolves to.
  if [ -f .env.adl ]; then echo "  (loading .env.adl)"; set -a; . ./.env.adl; set +a; fi
  ROUTER_HOST="$ROUTER_BASE"
  echo "→ router        $ROUTER_HOST   (REAL — cloud/tier calls WILL be made)"
else
  ROUTER_HOST="http://localhost:$MOCK_PORT"
  echo "→ mock router   :$MOCK_PORT   (canned responses — no cloud API calls)"
  MOCK_ROUTER_PORT="$MOCK_PORT" "$BIN/python" scripts/mock_router.py >data/mock_router.log 2>&1 &
  PIDS+=($!)
  wait_up "http://localhost:$MOCK_PORT/healthz" "mock router" data/mock_router.log
fi

echo "→ backend       :$BACKEND_PORT   (router → $ROUTER_HOST)"
ROUTER_BASE="$ROUTER_HOST" ROUTER_BASE_URL="$ROUTER_HOST/v1" \
  "$BIN/python" -m uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
  >data/backend.log 2>&1 &
PIDS+=($!)
wait_up "http://localhost:$BACKEND_PORT/openapi.json" "backend" data/backend.log

echo "→ frontend      :$FRONTEND_PORT"
echo
echo "   ▶ open  http://localhost:$FRONTEND_PORT     (Ctrl-C here stops everything)"
echo
VITE_API_URL="http://localhost:$BACKEND_PORT" VITE_WS_URL="ws://localhost:$BACKEND_PORT" \
  npm --prefix frontend run dev -- --host --port "$FRONTEND_PORT" --strictPort
