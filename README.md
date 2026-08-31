# deployd — pull-style deploy agent over HTTPS

*Léelo en [español](README-ES.md).*

Deploy to your own servers from GitHub Actions with **no inbound SSH or FTP,
no build toolchain on prod, and no containers**. One HTTPS endpoint with a
fixed, HMAC-signed contract is the entire attack surface.

## Features

- **Signed deploys** — HMAC-SHA256 per app over timestamp, nonce, and body;
  atomic persisted replay protection and constant-time compares
- **Artifact-based** — CI builds and publishes; the server downloads and
  verifies the SHA256; prod never compiles anything
- **Safe cutover, instant rollback** — immutable `releases/<sha>-<deploy_id>/`
  attempts plus a `current` symlink (atomic on Linux) or guarded junction swap
  (Windows); failed health checks roll back automatically
- **Migrations that gate the release** — forward-only, checksummed SQL
  migrations (`deployd-migrate`, SQL Server via pyodbc) run before cutover
  and halt the deploy hard on failure
- **Admin UI** — app registry, one-click secret rotation (shown once),
  deploy history with per-step logs, redeploy, live status
- **Bare-host native** — systemd on Linux, NSSM/IIS on Windows; a single
  Python service with a SQLite state file
- **Crash-aware queue** — queued deploys resume after restart; interrupted
  deploys fail explicitly instead of remaining stuck in `running`

## How it works

```
GitHub Actions (build + publish artifact)
      |
      |  HTTPS + HMAC (timestamp + nonce, per-app secret)
      v
Deploy API (FastAPI)  -- validate, enqueue, 202 + deploy_id
      |
      v
Deploy Worker (per-app serialized queue)
      +--> download artifact + verify SHA256
      +--> unpack into unique release dir  (traversal + resource-limit safe)
      +--> run migrations                  (forward-only, halt on failure)
      +--> cutover                         (symlink or guarded junction swap)
      +--> restart + health check          (fail => auto-rollback)
      +--> record status + step log        (CI polls GET /deploys/{id})
```

### Design decisions

- **Artifacts, not source.** What was signed is what runs; prod needs no SDKs.
- **Deploy by commit SHA, never branch name.**
- **API and worker are separate.** The API only validates and enqueues;
  same-app deploys serialize, different apps run concurrently.
- **A fixed contract narrows the blast radius.** Unlike a self-hosted runner,
  the workflow cannot replace deploy commands or target an unregistered app.
  A compromised build can still ship malicious application code, so run each
  deployed app with its own least-privileged identity.
- **No external database.** Config is YAML + env; runtime state is SQLite.
  A deploy agent must not depend on infrastructure it might be deploying.

## Quickstart

```bash
brew install uv pnpm                            # or install them for your OS
make install                                   # python venv + deps, web deps (pnpm)
cp .env.example .env                           # set DEPLOYD_ADMIN_TOKEN
cp config/apps.example.yaml config/apps.yaml   # register your apps
make dev                                       # API on 127.0.0.1:8300
make dev-web                                   # admin UI (Vite dev server)
```

Open the UI, enter the admin token, and rotate your app's secret — that value
becomes the `DEPLOYD_SECRET` in your app repo's CI.

## CI integration

Copy [`examples/github-actions-deploy.yml`](examples/github-actions-deploy.yml)
into your app repo and vendor
[`examples/notify_deploy.py`](examples/notify_deploy.py) as
`scripts/notify_deploy.py`. The repo needs one secret (`DEPLOYD_SECRET`) and
one variable (`DEPLOYD_URL`) — the server stores no GitHub credentials at all.

The request contract:

```
POST /deploys
X-Deploy-Timestamp: <unix epoch seconds>
X-Deploy-Nonce: <uuid4>
X-Deploy-Signature: sha256=<hex hmac of "{timestamp}.{nonce}.{raw body}">

{
  "app": "example-api",
  "commit_sha": "<40-hex git sha>",
  "artifact_url": "https://...",
  "artifact_sha256": "<64-hex>",
  "triggered_by": "github-actions:<run_id>"
}
```

`202 {deploy_id}` → poll `GET /deploys/{deploy_id}` for
`queued | running | succeeded | failed | rolled_back` plus the step log.
If the `202` response is lost, retry the exact signed request with the same
nonce; deployd returns the original `deploy_id` without enqueueing a duplicate.

## Production

- **Linux:** [`deploy/deployd.service`](deploy/deployd.service) — systemd
  unit, dedicated user, per-app sudoers rules for restarts.
- **Windows:** [`deploy/windows.md`](deploy/windows.md) — NSSM service,
  IIS physical path on a junction, `Restart-WebAppPool`.
- **Hardening:** bind to localhost behind a reverse proxy, keep `/admin` off
  the public internet — full checklist in [`SECURITY.md`](SECURITY.md).

Run exactly one deployd process per state database; that process owns the
durable per-app queues. Build `web/` and serve `web/dist` from the reverse
proxy, forwarding `/api/*` to deployd after stripping the `/api` prefix.
For database changes, use expand/contract migrations so the previous release
remains compatible if application rollback is required.

## Layout

```
src/deployd/
  main.py                  FastAPI app factory + lifespan (starts worker)
  config.py                settings, app registry, secrets
  security.py              HMAC verification (signature, window, nonce)
  models.py                request/response schemas
  api/routes.py            POST /deploys, GET /deploys/{id}, GET /healthz
  api/admin.py             /admin: registry CRUD, secret rotation, redeploy, history
  worker/queue.py          per-app serialized asyncio queue
  worker/runner.py         the seven-step deploy pipeline
  migrate.py               deployd-migrate CLI
  store/db.py              SQLite state store
web/                       React admin UI (Vite + Tailwind)
examples/                  CI workflow + vendorable notify script
deploy/                    systemd unit, Windows guide
tests/                     pytest (API/worker) — web/ has vitest
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security reports:
[`SECURITY.md`](SECURITY.md).

## License

Apache-2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
