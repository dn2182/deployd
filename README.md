# deployd — pull-style deploy agent over HTTPS

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Léelo en [español](README-ES.md).*

> **Status:** early preview. The core is tested, but `0.1.0` remains unreleased
> until the first production deployment and rollback drill are complete.

Deploy application artifacts to your own servers from GitHub Actions with
**no inbound SSH or FTP and no application build toolchain or containers in
the deployment path**. One HTTPS endpoint with a fixed, HMAC-signed contract
is the entire attack surface.

## Features

- **Signed deploys** — HMAC-SHA256 per app over timestamp, nonce, and body;
  atomic persisted replay protection and constant-time compares
- **Artifact-based** — CI builds and publishes; the server downloads and
  verifies the SHA256; deployed applications are never compiled on the server
- **Safe cutover, local rollback** — real `releases/current/` with atomic
  directory exchange, or the legacy immutable `releases/<sha>-<deploy_id>/`
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
      +--> cutover                         (directory exchange or legacy link swap)
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

## Install on Ubuntu

Git is the only bootstrap dependency. The guided installer installs the
remaining system and project requirements, runs validation, and configures the
service, Nginx, runtime state, and management UI.

```bash
sudo apt update
sudo apt install -y git
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /opt/deployd
git clone https://github.com/dn2182/deployd.git /opt/deployd
cd /opt/deployd
./deploy/install-ubuntu.sh
```

Run [`deploy/install-ubuntu.sh`](deploy/install-ubuntu.sh) as the normal
repository owner, not with `sudo`. It elevates only operations that require
system access. Cloudflare Flexible mode is provided for testing only; keep the
management port firewall-restricted and move to Full (strict) before production.
The installer prompts for the public domain, management bind/port, and Basic
Auth username, then generates the admin token when needed.
It repairs missing or unused development paths in an existing `.env`, keeps
existing tokens and absolute paths, and backs up `.env` before changes. If a
development path contains state, it stops with migration instructions rather
than switching to an empty database. Runtime paths and app configuration are
checked as the service user before startup.

## Update on Ubuntu

```bash
cd /opt/deployd
git pull --ff-only origin main
make install
make build
sudo systemctl restart deployd
```

For frontend-only changes, `git pull --ff-only origin main` followed by
`make build` is enough; refresh the browser afterward.

## Uninstall from Ubuntu

```bash
cd /opt/deployd
./deploy/uninstall-ubuntu.sh
```

[`deploy/uninstall-ubuntu.sh`](deploy/uninstall-ubuntu.sh) offers a
permission-restricted backup and requires explicit confirmation. It removes
deployd's service, Nginx configuration, credentials, runtime state, service
account, and repository. Shared packages and deployed applications are
preserved because they may be used independently.

## Local development

Install Python 3.11+, `uv`, Node.js 22.19+, and pnpm 11.20.0 for your operating
system, then:

```bash
make install                                   # Python + frontend dependencies
cp .env.example .env                           # adjust paths below and set admin token
cp config/apps.example.yaml config/apps.yaml   # register your applications
make dev                                       # API on 127.0.0.1:8300
make dev-web                                   # Vite management UI
```

Before `make dev`, set `DEPLOYD_DB_PATH=deployd.sqlite3`,
`DEPLOYD_APPS_CONFIG=config/apps.yaml`, and
`DEPLOYD_SECRETS_FILE=config/secrets.env` in `.env`. The example defaults to
Ubuntu service paths under `/var/lib/deployd`.

## Guided application setup

Open the management UI over HTTPS or an SSH tunnel and enter the admin token.
Choose **Add application** to configure:

- The app name, GitHub repository (`OWNER/REPO` or its URL), and public deployd
  API URL. This API URL is separate from the website being deployed.
- Static website or service, release paths, health URL, and retention. Defaults
  keep live files in `/srv/deployd/<app>/releases/current` and retain one previous
  version. Static mode checks `index.html`; Nginx remains the web server.
- A random signing secret (shown once), or your own secret of at least 32 bytes.
- An optional per-app, read-only GitHub token for private release assets.

**Edit** uses the same form. Blank credential fields preserve existing values;
replacing a configured signing secret requires confirmation. **Advanced JSON**
remains available for migrations and other settings. Apps with existing releases
cannot change their release paths or layout through this form.

Credentials are stored separately in `DEPLOYD_SECRETS_FILE` with mode `0600`.
GitHub tokens are never returned by the API. Per-app environment values
(`DEPLOYD_SECRET_<APP_NAME_UPPER_SNAKE>` and
`DEPLOYD_GITHUB_TOKEN_<APP_NAME_UPPER_SNAKE>`) take precedence and cannot be changed
from the UI. App names use hyphens, replaced with underscores in these keys.

The completion screen provides the GitHub settings link and CI instructions.
Saving configures deployd; it does **not** add GitHub secrets/workflows, test the
GitHub credential, alter Nginx, or deploy the app. Copy the signing secret into
the repository's `DEPLOYD_SECRET`, set the `DEPLOYD_URL` Actions variable, and add
the workflow below. Configure Nginx's document root or fixed web-root symlink
separately. An HTTP health check confirms availability, not the live version;
check the first release before switching an existing site to its new path.

## CI integration

Copy [`examples/github-actions-deploy.yml`](examples/github-actions-deploy.yml)
into your app repo and vendor
[`examples/notify_deploy.py`](examples/notify_deploy.py) as
`scripts/notify_deploy.py`. The repo needs one secret (`DEPLOYD_SECRET`) and
one variable (`DEPLOYD_URL`). Public release downloads need no GitHub credential
on the server.

For a private repository, add a fine-grained token with **Contents: read** for
that repository in the app form; no service restart is needed. The server-wide
`DEPLOYD_GITHUB_TOKEN` in the protected `.env` remains a fallback when no per-app
token exists (restart after changing `.env`). Removing an app token re-enables
that fallback. Keep these tokens separate from the admin token and app HMAC
secret. The workflow uses its built-in `github.token` to publish the release.

The reference workflow sends the asset's API URL:
`https://api.github.com/repos/OWNER/REPO/releases/assets/ASSET_ID`.
The guided form sets `artifact.allowed_url_prefix` to that repository's
`https://api.github.com/repos/OWNER/REPO/releases/assets/` prefix and includes
`release-assets.githubusercontent.com` in `artifact.allowed_redirect_hosts`.
Deployd requests binary content and sends the read-only token only on the initial
HTTPS GitHub asset API request; redirects never receive it. Public repositories
can use the same API URLs without `DEPLOYD_GITHUB_TOKEN`.

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

## Version management

Open **Manage versions** on an application to view its local releases, activate
an older version, or delete an unneeded version's files with confirmation.
Activation uses the same per-app queue, restarts the app, and checks health.
It does not download artifacts or run migrations; older code must still work
with the current database. A failed activation attempts to restore the prior
version. Interrupted activations are marked failed, not replayed on restart;
inspect the active version before retrying.

### Real current folder

New applications in the UI default to **Real current folder (Linux/macOS)**.
Enter the app name and the UI fills `/srv/deployd/<app>/releases` and its
`current` path. Configure the artifact allowlist, restart command, and health
URL for that app before saving. The service creates the folders and probes
atomic exchange support on that filesystem; it does not configure Nginx.

```text
/srv/deployd/bluedatos/releases/
  current/                         # real live files, not a symlink
  <previous-sha>-<deployment-id>/   # retained real directory
```

The configuration is `release_layout: directory`, with
`current_link: /srv/deployd/bluedatos/releases/current`. The legacy field name
`current_link` now identifies the active path in either layout. A first GitHub
deployment creates `current`; subsequent deployments exchange directories
atomically, then archive the displaced version under its original identity.
The active release has no duplicate version-named directory. **Activate** uses
the same exchange and health checks; **Delete files** removes only an eligible
retained version. The default remains current plus one rollback version.

Nginx may serve `releases/current` directly, or use a fixed symlink such as
`/var/www/bluedatos.com` pointing to it. That external link never changes and
must be set up separately, after the first release is ready. Do not replace an
existing live document root with a dangling link.

The Ubuntu installer creates `/srv/deployd` owned by `deployd`. Existing
installations upgrading without rerunning the installer need this once:

```bash
sudo install -d -o deployd -g deployd -m 0755 /srv/deployd
```

Custom roots must also be writable by the service. Web-server access to release
contents is separate: for static sites, use a tar artifact containing `.` with
directories mode `0755` and files `0644`, and verify Nginx-user readability.
ZIP extraction under the service's restrictive umask does not grant that access.
Never package secrets in a publicly served release.

This mode requires same-filesystem atomic directory exchange (Linux
[renameat2(RENAME_EXCHANGE)](https://man7.org/linux/man-pages/man2/rename.2.html),
or macOS `renamex_np(RENAME_SWAP)`). There is no
copy-in-place or two-rename fallback. Windows uses the existing symlink/junction
layout. Atomic cutover does not make database changes or browser asset caching
atomic.

Each directory has a reserved `.deployd-release.json` identity file; artifacts
must not supply it. Keep it with the files if moving a version manually. Startup
reconciles archive names after an interrupted process without deleting releases.
Invalid/duplicate identities block that app's version operations rather than
overwrite user data; the management API stays available for diagnosis.
If working manually, stop deployd first and use an atomic exchange, not a copy
over the live files; restart deployd to reconcile names and verify site health.
Manual changes do not create deployment-history events. Prefer the UI for an
audited rollback. Failed attempts after cutover retain their files for inspection
until manual cleanup or a later successful deployment's retention pass.

Apps without `release_layout` keep the old `symlink` behavior. Layout and path
changes are blocked while an app has existing releases: use a new app or perform
an explicitly planned offline migration. Existing live symlinks are never
silently converted to directories.

By default, retain two releases total: the active version and one previous
version for rollback. Settings are saved per application and can be changed:

- `keep_previous: 1` (default): retain the active version **plus one previous version**.
- `keep_previous: 3`: retain the active version **plus three previous versions**.
- `keep_previous: 0`: keep only the active version after automatic cleanup;
  local rollback is then unavailable.
- `auto_cleanup: true` (default): trim excess versions after a successful new
  artifact deployment. Set `false` to keep cleanup entirely manual.

Saving settings does not delete files immediately. During deployment, the old
version remains available for automatic rollback even when retention is off.
The active version is always protected from cleanup. With retention enabled,
the immediately previous version is protected too. Other
retained versions can be removed manually; deployment history stays in SQLite.
In directory mode, the metadata in `current` identifies the prior version.
Legacy symlink mode uses a sibling `current.previous` link instead; do not use
that path for other files. Imported sites outside the managed releases directory
are never deleted.

Existing `keep_releases` configurations remain supported: their total count is
converted to `keep_previous = keep_releases - 1`, preserving the existing policy.
For example, the old `keep_releases: 5` becomes `keep_previous: 4`.
An earlier `keep_previous: null` now uses the default of one previous version.

## Deployment notes

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
