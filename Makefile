# Developer commands. Everything runs from .venv; nothing here downloads a model.
PYTHON ?= $(shell for p in python3.13 python3.14 python3.12 python3.11; do command -v $$p >/dev/null && { echo $$p; break; }; done)
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help setup hooks test test-fast lint fix typecheck check tools clean

help:            ## show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup:           ## create .venv and install tara with every extra
	@test -n "$(PYTHON)" || { echo "Need Python 3.11+ (brew install python@3.13)"; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[all]"

hooks:           ## run ruff and mypy before every commit
	$(BIN)/pre-commit install

test:            ## offline suite (live API and external tiers stay opt-in)
	$(BIN)/python -m pytest -q

test-fast:       ## offline suite without the real-Chrome browser tests
	$(BIN)/python -m pytest -q -m "not live_api and not external and not browser"

lint:            ## ruff
	$(BIN)/ruff check src tests

fix:             ## ruff with safe fixes (imports, unused names)
	$(BIN)/ruff check --fix src tests

typecheck:       ## mypy
	$(BIN)/mypy src

check: lint typecheck test   ## everything CI would run

tools:           ## list every tool the model can call
	$(BIN)/tara tools

clean:           ## remove caches
	find . -name __pycache__ -prune -exec rm -rf {} + ; rm -rf .pytest_cache .mypy_cache .ruff_cache
