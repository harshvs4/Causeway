# Anchorline. `make setup` once, then `make build`.
PY := .venv/bin/python

.PHONY: setup build vault serve test clean all

setup:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

build:
	$(PY) -m engine.build

vault:
	@echo "vault: not implemented until Phase 3"

serve:
	@echo "serve: not implemented until Phase 5"

test:
	$(PY) -m pytest -q

clean:
	$(RM) -r build vault .pytest_cache

all: build test
