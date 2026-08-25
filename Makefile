VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: install clean dev dev-web update test

install:
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"
	cd web && pnpm install

dev:
	$(VENV)/bin/uvicorn deployd.main:app --host 127.0.0.1 --port 8300 --reload

dev-web:
	cd web && pnpm dev

test:
	$(VENV)/bin/pytest -q

update:
	$(PIP) install --upgrade pip
	$(PIP) install --upgrade -e ".[dev]"
	cd web && pnpm update

clean:
	rm -rf dist build src/*.egg-info .pytest_cache .ruff_cache web/dist
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -not -path "./web/node_modules/*" -exec rm -rf {} +
