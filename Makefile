VENV := .venv
UV   ?= uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.python
ifneq ($(wildcard $(CURDIR)/.node/bin/node),)
export PATH := $(CURDIR)/.node/bin:$(PATH)
endif

# Runs project tooling in a throwaway environment so a live .venv is never modified.
UVRUN := $(UV) run --extra dev --frozen --isolated
PY_SOURCES := src tests deploy/runtime_config.py deploy/connect_website.py

.PHONY: install install-web build clean dev dev-web update test lint audit check

install:
	$(UV) sync --extra dev --frozen
	cd web && pnpm install --frozen-lockfile

install-web:
	cd web && pnpm install --frozen-lockfile

build:
	cd web && pnpm build

dev:
	$(VENV)/bin/uvicorn deployd.main:app --host 127.0.0.1 --port 8300 --reload

dev-web:
	cd web && pnpm dev

test:
	$(VENV)/bin/pytest -q
	cd web && pnpm test

lint:
	$(VENV)/bin/ruff check $(PY_SOURCES)
	$(VENV)/bin/ruff format --check $(PY_SOURCES)
	cd web && pnpm lint

audit:
	$(UV) run --with pip-audit==2.10.1 pip-audit
	cd web && pnpm audit --prod

check:
	$(UVRUN) ruff check $(PY_SOURCES)
	$(UVRUN) ruff format --check $(PY_SOURCES)
	$(UVRUN) pytest -q
	$(UVRUN) --with pip-audit==2.10.1 pip-audit
	cd web && pnpm lint && pnpm test && pnpm audit --prod

update:
	$(UV) lock --upgrade
	$(UV) sync --extra dev --frozen
	cd web && pnpm update

clean:
	rm -rf dist build src/*.egg-info .pytest_cache .ruff_cache web/dist
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -not -path "./web/node_modules/*" -exec rm -rf {} +
