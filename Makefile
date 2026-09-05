# Anchorline. `make setup` once, then `make build`.
PY := .venv/bin/python

.PHONY: setup build vault console console-data mcp assist restart serve test clean all

setup:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

build:
	$(PY) -m engine.build

vault: build
	$(PY) vault_build.py

mcp:
	$(PY) -m mcp_server.server

console-data: build
	mkdir -p console/public
	cp build/facts.json console/public/facts.json

console: console-data
	cd console && npm run dev

assist:
	$(PY) -m assist.app

serve: assist

test:
	$(PY) -m pytest -q

restart: build console-data
	@echo "restarting services so none of them serves a stale build..."
	@pkill -f "mcp_server.server" 2>/dev/null || true
	@pkill -f "assist.app" 2>/dev/null || true
	@sleep 1
	@($(PY) -m mcp_server.server > /tmp/anchorline_mcp.log 2>&1 &) ; \
	 ($(PY) -m assist.app > /tmp/assist.log 2>&1 &) ; \
	 sleep 4
	@curl -sf http://localhost:8848/mcp -o /dev/null \
	  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
	  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
	  && echo "  mcp    :8848 ok" || echo "  mcp    :8848 FAILED"
	@curl -sf http://127.0.0.1:8765/health -o /dev/null \
	  && echo "  assist :8765 ok" || echo "  assist :8765 FAILED"
	@echo "  console: vite serves public/facts.json from disk, now resynced"

clean:
	$(RM) -r build vault .pytest_cache

all: build test
