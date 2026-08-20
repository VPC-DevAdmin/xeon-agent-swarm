# Agent Orchestrator — one-time setup, one-command demo.
#
#   make setup      install everything (Python venv + frontend deps)
#   make demo       launch the whole stack against the MOCK router (no cloud calls)
#   make demo-live  launch against the REAL tier router (ROUTER_BASE / .env.adl)
#
# `demo` starts three local processes — mock router, backend API, frontend —
# and holds the terminal. Ctrl-C stops all three. Open the URL it prints.
# (No ssh-tunnel logic here; forward the frontend + backend ports yourself if
#  you drive it from another machine.)

# Portable across make 3.81 (macOS) and 4.x: every recipe is a single command,
# so process orchestration lives in scripts/dev_stack.sh, not in make syntax.

# ---- overridable knobs: e.g. `make demo BACKEND_PORT=8020` --------------------
PY            ?= python3
VENV          ?= .venv
BACKEND_PORT  ?= 8010
FRONTEND_PORT ?= 3000
MOCK_PORT     ?= 8901
# the REAL router for `make demo-live` (keep it a bare URL, no trailing spaces)
ROUTER_BASE   ?= http://localhost:8900

BIN := $(VENV)/bin
STACK_ENV = BIN=$(BIN) BACKEND_PORT=$(BACKEND_PORT) FRONTEND_PORT=$(FRONTEND_PORT) MOCK_PORT=$(MOCK_PORT)

.DEFAULT_GOAL := help
.PHONY: help setup demo demo-live serve test reset-db stop clean

help:
	@echo ""
	@echo "  Agent Orchestrator"
	@echo "  ------------------"
	@echo "  make setup      install Python + frontend dependencies (run once)"
	@echo "  make demo       run the full stack with the MOCK router (safe, offline)"
	@echo "  make demo-live  run the full stack against the REAL router ($(ROUTER_BASE))"
	@echo "  make serve      PRODUCTION: build the SPA + serve UI/API/WS on ONE port"
	@echo "  make test       run the backend test suite"
	@echo "  make reset-db   delete the app database (recreated on next start)"
	@echo "  make stop       kill anything left listening on the demo ports"
	@echo "  make clean      remove the venv and frontend node_modules"
	@echo ""
	@echo "  After 'make demo', open  http://localhost:$(FRONTEND_PORT)"
	@echo ""

setup:
	@echo "→ Python venv + backend deps ($(PY))"
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r backend/requirements.txt
	$(BIN)/pip install -q pytest
	@echo "→ Frontend deps (npm)"
	@# npm ci installs strictly from package-lock.json and never rewrites it, so a
	@# different local npm version can't leave the lockfile "modified". Falls back to
	@# install only when there is no lockfile yet.
	@if [ -f frontend/package-lock.json ]; then npm --prefix frontend ci; else npm --prefix frontend install; fi
	@echo ""
	@echo "✓ setup complete — now run:  make demo"

demo:
	@$(STACK_ENV) bash scripts/dev_stack.sh mock

demo-live:
	@$(STACK_ENV) ROUTER_BASE=$(ROUTER_BASE) bash scripts/dev_stack.sh live

# Production single-origin mode: the backend serves the built SPA, so the UI, REST,
# and WebSocket share one origin/port — what a Cloudflare Tunnel points at. Binds to
# localhost by default (the tunnel connects locally); override with SERVE_HOST=0.0.0.0.
SERVE_HOST ?= 127.0.0.1
serve:
	npm --prefix frontend run build
	$(BIN)/python -m uvicorn backend.main:app --host $(SERVE_HOST) --port $(BACKEND_PORT)

test:
	$(BIN)/python -m pytest tests/ -q

reset-db:
	rm -f data/orchestrator.db
	@echo "✓ app DB removed — a fresh schema is created on the next backend start"

stop:
	@for p in $(BACKEND_PORT) $(MOCK_PORT) $(FRONTEND_PORT); do \
	  if command -v lsof >/dev/null 2>&1; then \
	    pids=$$(lsof -ti tcp:$$p 2>/dev/null); \
	    if [ -n "$$pids" ]; then echo "killing :$$p ($$pids)"; kill $$pids 2>/dev/null || true; fi; \
	  elif command -v fuser >/dev/null 2>&1; then \
	    fuser -k $$p/tcp >/dev/null 2>&1 && echo "killed :$$p" || true; \
	  fi; \
	done; \
	pkill -f 'vite --host --port $(FRONTEND_PORT)' 2>/dev/null || true

clean: stop
	rm -rf $(VENV) frontend/node_modules
	@echo "✓ removed venv + node_modules (run 'make setup' to rebuild)"
