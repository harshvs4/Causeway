# Anchorline. `make setup` once, then `make build`.
PY := .venv/bin/python

.PHONY: setup build vault console console-data mcp assist serve test clean all

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

clean:
	$(RM) -r build vault .pytest_cache

all: build test
