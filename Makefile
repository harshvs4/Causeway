# Anchorline. `make setup` once, then `make build`.
PY := .venv/bin/python

.PHONY: setup build vault console console-data serve test clean all

setup:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

build:
	$(PY) -m engine.build

vault: build
	$(PY) vault_build.py

console-data: build
	mkdir -p console/public
	cp build/facts.json console/public/facts.json

console: console-data
	cd console && npm run dev

serve:
	@echo "serve: not implemented until Phase 5"

test:
	$(PY) -m pytest -q

clean:
	$(RM) -r build vault .pytest_cache

all: build test
