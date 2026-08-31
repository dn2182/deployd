VENV := .venv
PY   := $(VENV)/bin/python
UV   ?= uv

.PHONY: install clean dev dev-web update test lint audit

install:
	$(UV) sync --extra dev --frozen
	cd web && pnpm install --frozen-lockfile

dev:
	$(VENV)/bin/uvicorn deployd.main:app --host 127.0.0.1 --port 8300 --reload

dev-web:
	cd web && pnpm dev

test:
	$(VENV)/bin/pytest -q
	cd web && pnpm test

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests
	cd web && pnpm lint

audit:
	$(UV) run --with pip-audit==2.10.1 pip-audit
	cd web && pnpm audit --prod

update:
	$(UV) lock --upgrade
	$(UV) sync --extra dev --frozen
	cd web && pnpm update

clean:
	rm -rf dist build src/*.egg-info .pytest_cache .ruff_cache web/dist
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -not -path "./web/node_modules/*" -exec rm -rf {} +
