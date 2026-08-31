# Contributing

Thanks for your interest in deployd.

## Setup

Install `uv` and `pnpm`, then:

```bash
make install     # python venv + deps, web deps (pnpm)
make test        # pytest + vitest
make lint        # ruff + frontend lint
make audit       # Python + production Node dependency advisories
make dev         # API on 127.0.0.1:8300 (hot reload)
make dev-web     # Vite dev server, proxies /api to the API
```

## Guidelines

- Keep the scope small: deployd deploys artifacts; it is not a CI system,
  an observability stack, or a secrets manager.
- Every behavior change needs a test (pytest for the API/worker, vitest for
  the UI).
- `ruff format` before committing; CI enforces lint and formatting.
- Security-sensitive changes (auth, extraction, subprocess handling) get
  extra scrutiny — explain the reasoning in the PR description.
- By contributing you agree your contributions are licensed under Apache-2.0.
