# deployd — pull-style deploy agent over HTTPS

Small, self-contained deploy service. GitHub Actions builds an artifact and
POSTs a signed deploy request to this API; a local worker downloads, verifies,
migrates, cuts over, and health-checks — **no inbound SSH or FTP on the server,
ever.** One HTTPS endpoint with a fixed contract is the entire attack surface.


## Architecture

```
GitHub Actions (build + publish artifact)
      |
      |  HTTPS + HMAC (timestamp + nonce, per-app secret)
      v
Deploy API (FastAPI)  -- validate, enqueue, 202 + deploy_id
      |
      v
Deploy Worker (per-app serialized queue)
      +--> download artifact (URL from payload) + verify SHA256
      +--> unpack into releases/<sha>/
      +--> run migrations        (forward-only, halt hard on failure)
      +--> cutover               (symlink/pointer swap to new release)
      +--> health check          (N retries; fail => auto-rollback)
      +--> record status + logs  (Actions polls GET /deploys/{id})
```

### Design decisions (and why)

- **Artifacts, not source.** The server never builds. Actions compiles,
  publishes the artifact, and sends its URL + SHA256. Prod needs no SDKs,
  deploys are reproducible, and what was signed is what runs.
- **Deploy by commit SHA, never branch name.** The payload pins the exact
  release identity.
- **Releases dir + pointer swap.** `releases/<sha>/` + `current` symlink
  (or IIS physical-path repoint on Windows). Cutover is atomic; rollback is
  instant; a failed deploy never touches the running version.
- **HMAC, done properly.** Signature over `timestamp.body`, per-app secret,
  ±5 min timestamp window, nonce replay store, constant-time compare.
- **API and worker are separate.** API only validates and enqueues (202 with
  `deploy_id`). Worker holds a per-app lock — deploys serialize, never overlap.
- **Migrations gate the cutover.** Run before the pointer swap; a failed
  migration halts the deploy hard (schema can't be rolled back by symlink).
- **Fixed contract beats a self-hosted runner.** A runner executes whatever
  the workflow says — compromised action = RCE on prod. This API can only do
  the steps above, against allowlisted apps.

### State: files for config, SQLite for runtime

- `config/apps.yaml` — app registry: paths, artifact rules, health URL,
  restart command, migration runner. Secrets via env / `.env` (never in git).
- `deployd.sqlite3` — deploy runs, statuses, logs, nonce replay store.
  A deploy agent must not depend on an external DB server (bootstrap problem);
  its state is tiny. All access is parameterized queries isolated in
  `store/db.py`, the only module that touches the database.

## Layout

```
config/apps.example.yaml   app registry template
src/deployd/
  main.py                  FastAPI app factory + lifespan (starts worker)
  config.py                settings + app registry loading
  security.py              HMAC verification (signature, window, nonce)
  models.py                Pydantic request/response schemas
  api/routes.py            POST /deploys, GET /deploys/{id}, GET /healthz
  api/admin.py             /admin: registry CRUD, secret rotation, redeploy, history
  worker/queue.py          per-app serialized asyncio queue
  worker/runner.py         deploy pipeline: download→verify→migrate→cutover→health
  migrate.py               deployd-migrate CLI: forward-only SQL migrations, GO batches
  store/db.py              SQLite state store (only module that touches the DB)
tests/
examples/
  github-actions-deploy.yml  reference CI workflow (copy into your app repo)
  notify_deploy.py           stdlib-only signer/poller to vendor into app repos
web/                       React admin UI (Vite + Tailwind)
deploy/
  deployd.service          systemd unit + server setup notes
  windows.md               Windows guide (NSSM, IIS, junctions)
```

## Quickstart

```bash
make install                       # python venv + deps, web deps (pnpm)
cp .env.example .env               # set DEPLOYD_ADMIN_TOKEN, per-app secrets
cp config/apps.example.yaml config/apps.yaml   # register your apps
make dev                           # API on 127.0.0.1:8300
make dev-web                       # admin UI (dev) on the Vite port
make test                          # pytest + vitest
```

Production: see `deploy/deployd.service` (Linux/systemd) or
`deploy/windows.md` (Windows/NSSM/IIS). CI side: copy
`examples/github-actions-deploy.yml` and `examples/notify_deploy.py` into
your app repo.

## Deploy request contract

```
POST /deploys
X-Deploy-Timestamp: <unix epoch seconds>
X-Deploy-Nonce: <uuid4>
X-Deploy-Signature: sha256=<hex hmac of "{timestamp}.{raw body}">

{
  "app": "example-api",
  "commit_sha": "<40-hex git sha>",
  "artifact_url": "https://...",
  "artifact_sha256": "<64-hex>",
  "triggered_by": "github-actions:<run_id>"
}
```

Responses: `202 {deploy_id}` → poll `GET /deploys/{deploy_id}` for
`queued | running | succeeded | failed | rolled_back` + step log.

## Roadmap

- [x] M1: API skeleton — HMAC auth, contract validation, SQLite store, status endpoint
- [x] M2: Worker — download/verify/unpack, symlink cutover, restart, health check, rollback
- [x] M3: Migration runner hook (SQL Server, forward-only, versioned table)
- [x] M4: GitHub Actions reference workflow, signed notify script, systemd unit, e2e test
- [ ] M4b: production rollout on a real Linux VPS
- [x] M5: Windows variant — junction cutover, NSSM/IIS guide (`deploy/windows.md`)
- [x] M6: Admin API (registry CRUD, secret rotation, redeploy, deploy history) + `web/` UI

## License

Apache-2.0 — see `LICENSE` and `NOTICE`.
