# deployd — pull-style deploy agent over HTTPS

[![CI](https://github.com/dn2182/deployd/actions/workflows/ci.yml/badge.svg)](https://github.com/dn2182/deployd/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/dn2182/deployd)](LICENSE)

*Léelo en [español](README-ES.md).*

Deploy application artifacts to your own servers from GitHub Actions with
**no inbound SSH or FTP and no build toolchain or containers on the server**.
CI builds and publishes a release asset, then sends one HMAC-signed request;
deployd downloads it, verifies it, swaps it live atomically, restarts the app,
checks its health and rolls back by itself if that fails.

> **Status:** early preview. `0.1.0` is tagged after the first production
> deployment and rollback drill.

## Contents

1. [How it works](#how-it-works)
2. [Install on Ubuntu](#install-on-ubuntu)
3. [Register an application](#register-an-application)
4. [Connect GitHub Actions](#connect-github-actions)
5. [Go live: connect the website](#go-live-connect-the-website)
6. [Operate](#operate)
7. [Reference](#reference)
8. [Local development](#local-development)

## How it works

```
GitHub Actions (build + publish release asset)
      |  HTTPS + HMAC (timestamp, nonce, per-app secret)
      v
deployd API ---- validate, enqueue, 202 + deploy_id
      |
      v
worker (one queue per app, apps in parallel)
      download + SHA256 -> unpack -> migrate -> before_cutover hook
      -> atomic cutover -> restart -> health (+ after_health hook)
      fail after cutover => automatic rollback
```

- **Artifacts, not source.** What CI signed is what runs; production needs no
  SDKs. Deploys are pinned to a commit SHA, never a branch name.
- **A fixed contract.** The workflow cannot choose commands or paths; it can
  only trigger an app you registered. A compromised build still ships what it
  built, so each app runs under its own least-privileged identity.
- **Nothing else is managed.** deployd never writes your app's Nginx site,
  systemd unit or configuration. Those exist before the first deploy.
- **No external database.** Config is YAML plus env; runtime state is SQLite.

## Install on Ubuntu

Git is the only bootstrap dependency; the installer fetches Node 24, uv and
pnpm as checksum-verified official builds and sets up the service, Nginx,
state directories and the management UI. Run it as your normal user, not with
`sudo`; it elevates only what needs root.

```bash
sudo apt update && sudo apt install -y git
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /opt/deployd
git clone https://github.com/dn2182/deployd.git /opt/deployd
/opt/deployd/deploy/install-ubuntu.sh
```

You are asked for:

| Prompt | Default | Unattended override |
|---|---|---|
| Public deployd domain | `deployd.example.com` | `DEPLOYD_INSTALL_DOMAIN` |
| Management bind address | `0.0.0.0` (LAN) | `DEPLOYD_INSTALL_ADMIN_BIND` |
| Management port | `844` | `DEPLOYD_INSTALL_ADMIN_PORT` |
| Basic Auth username | `deployd-admin` | `DEPLOYD_INSTALL_ADMIN_USER` |
| Basic Auth password | prompted | `DEPLOYD_INSTALL_ADMIN_PASSWORD` |
| Confirmations | prompted | `DEPLOYD_INSTALL_YES=1` |

What you get:

- **Public API** on `https://<domain>` (port 443, self-signed certificate in
  `/etc/deployd/tls/`; port 80 redirects). Put Cloudflare in **Full** mode. For
  **Full (strict)** replace the certificate pair with a Cloudflare Origin CA
  certificate and key at the same paths; the installer never overwrites a
  certificate it did not generate.
- **Management UI** on `http://<bind>:844` behind Basic Auth. The default bind
  is reachable on the LAN; keep that port off the WAN with the host firewall,
  or choose `127.0.0.1` and use an SSH tunnel.
- **Admin token** generated into `/opt/deployd/.env` (printed path, not value).
  Read it with `sudo grep DEPLOYD_ADMIN_TOKEN /opt/deployd/.env`.
- A `deployd` system user that owns `/var/lib/deployd` (state, app registry,
  secrets) and `/srv/deployd` (releases).

Optional at install time: the **website connection helper** (a root-owned
program behind one sudo rule) that can switch an existing static site under
`/var/www` or any root listed in `/etc/deployd/site-roots` to deployd releases.
See [Go live](#go-live-connect-the-website).

### Update

```bash
/opt/deployd/deploy/update-ubuntu.sh
```

Pulls, runs lint, tests and the web build **before** stopping the service,
refuses to stop while a deploy is running, backs up the SQLite state to
`/var/lib/deployd/backups/` (newest five kept), and restarts. All settings are
kept. Local changes to tracked files on the server are printed and discarded
first; your configuration lives outside the tracked tree. Dependency-audit
findings are reported, not fatal (`DEPLOYD_INSTALL_STRICT_AUDIT=1` to stop).
Running `install-ubuntu.sh` by hand on an existing install offers the same
in-place update; `--reconfigure` asks the questions again.

### Uninstall

```bash
/opt/deployd/deploy/uninstall-ubuntu.sh          # --purge also removes releases,
                                                 # recovery data, TLS files, tooling
```

Offers a backup, asks whether connected websites should be restored to real
folders or keep their symlinks, and removes the service, Nginx sites,
credentials and state. Deployed applications are preserved.

### Change the management password

Key icon in the UI top bar once logged in. It changes the Nginx Basic Auth
password through a restricted root helper; the admin token and app secrets
are unchanged, and the browser asks you to sign in again.

## Register an application

Open the management UI, enter the admin token, press **Add application**.
The **Help** button (top bar, available before login) answers the usual
questions in English and Spanish.

| Field | Notes |
|---|---|
| Name | lowercase, digits, dashes; becomes `/srv/deployd/<name>/` |
| GitHub repository | `OWNER/REPO`; sets the artifact allowlist automatically |
| Public deployd URL | `https://<domain>`, used by the generated workflow |
| Kind | **Static site** (checks `index.html`) or **Service** (your restart command) |
| Local site path | static sites only; the path Nginx already serves, under an allowed site root |
| Restart command | argv list, no shell; services usually `sudo systemctl restart <unit>` |
| Health URL | optional; failing restart or health after cutover rolls back |
| Expected body / header | optional, `{commit_sha}` placeholder: a stale process cannot pass |
| Hooks | `before_cutover` (new release dir) and `after_health` (live release, rolls back on failure) |
| Environment passthrough | service variables forwarded to commands, for example a DSN |
| Retention | previous versions kept for rollback (default 1) |
| Signing secret | generated and shown once; this goes to CI as `DEPLOYD_SECRET_<APP>` |
| GitHub access token | optional, `Contents: read` on that repo, needed for private repos |

Server-side prerequisites, per app, that deployd does not create:

- A systemd unit (or IIS pool on Windows) for services, and this sudoers line
  for the deployd user: `deployd ALL=(root) NOPASSWD: /usr/bin/systemctl restart <unit>`.
- The Nginx site serving `/srv/deployd/<app>/releases/current` directly, or an
  existing document root you will connect in [Go live](#go-live-connect-the-website).
- The app's own configuration and secrets, wherever it reads them from.

Commands run with a clean environment plus `DEPLOYD_APP`, `DEPLOYD_DEPLOY_ID`,
`DEPLOYD_COMMIT_SHA`, `DEPLOYD_RELEASE_DIR`, `DEPLOYD_CURRENT` and the variables
listed in **Environment passthrough**; the service's own secrets never reach
them. A script that needs root runs as `/usr/bin/sudo <script>` with a matching
`NOPASSWD` sudoers line.

One repository with a frontend and a backend is two applications, each with
its own secret and workflow. A GitHub token set in the app form applies without
a restart and overrides the server-wide `DEPLOYD_GITHUB_TOKEN` fallback in
`.env` (which needs a restart after a change).

## Connect GitHub Actions

On the app card open **GitHub Actions setup**:

1. Pick the build preset (pnpm, npm, plain HTML, or a custom command such as
   `dotnet publish -c Release -o dist` or `go build -o dist/api ./cmd/api`),
   the project folder (`.` or `frontend`) and the output folder.
2. Pick the **trigger**: pushes to a branch, or Git tags matching a pattern
   (`v*`, `web-v*`). The checkbox below decides automatic versus manual: checked,
   the push or tag deploys by itself; unchecked, you run the workflow from
   Actions and choose the branch or tag.
3. Optional **Deployment targets** (`web-01, web-02`) for mirror nodes, each
   running its own deployd with the app registered.
4. **Preview workflow** to read it, **Save settings and download ZIP** to get
   `.github/workflows/deployd.yml`, `scripts/notify_deploy.py` and
   `DEPLOYD-SETUP.md`. Unzip at the repository root.

Repository settings (Settings, Secrets and variables, Actions):

| | Single server | Deployment targets |
|---|---|---|
| Variable | `DEPLOYD_URL` | `DEPLOYD_URL_WEB_01`, `DEPLOYD_URL_WEB_02` |
| Secret | `DEPLOYD_SECRET_<APP>` (falls back to `DEPLOYD_SECRET`) | `DEPLOYD_SECRET_WEB_01`, `DEPLOYD_SECRET_WEB_02` |

Several apps in one repository: rename the workflow per app
(`deployd-api.yml`, `deployd-web.yml`), use distinct tag prefixes in tag mode,
one secret per app. The notifier script is shared.

What the workflow does: a read-only `build` job packages the output folder
(dotfiles allowed, credential files and `node_modules` rejected) and uploads
it; a `publish` job re-verifies the checksum, creates the
`deploy-<app>-<sha>` release, notifies deployd and prunes those releases beyond
the newest ten. With targets, a `deploy` matrix job notifies every node after
one publish. `workflow_dispatch` has a `dry_run` input; uncomment the
`environment:` line to require reviewers. Actions and runners are pinned.

The notifier signs the request, polls `GET /deploys/{id}` and fails the run on
`failed`, `rolled_back`, `superseded` (a newer push replaced it in the queue)
or `cancelled`. CI sees step names and statuses; command output is visible
only in the admin UI.

## Go live: connect the website

For static sites that an existing Nginx server block already serves from a
folder (`/var/www/example.com`, `/srv/sites/example.com`):

1. Deploy once so `/srv/deployd/<app>/releases/current` exists.
2. In the app card open **Website connection**, check the status, press
   **Connect website** and type the app name.

The folder is exchanged atomically with a symlink to `releases/current`; the
original files are kept as the `b4deployd` version, available for activation.
Nginx configuration is not touched. If the path is later replaced by a real
folder or another link, the tab reports it and **Fix connection** re-links it,
keeping the replacement under `/var/lib/deployd-connect`. Every deploy logs a
`website` step that warns when the path is not linked, and the status card
shows it.

Allowed site roots are `/var/www` plus every line of root-owned
`/etc/deployd/site-roots`; the installer checks each root is root-owned and
not group or world writable (`DEPLOYD_INSTALL_SITE_ROOTS=/srv/sites` adds one
unattended). Details, recovery and the restore-on-removal flow:
[docs/website-connection.md](docs/website-connection.md).

If you would rather point Nginx's `root` at `releases/current` yourself, do
that and skip the connector.

## Operate

- **Status card** per app: current release, queued count, last deploy, last
  health, website link state, **Freeze** (CI deploys answer `423` until
  unfrozen; rollback keeps working).
- **Recent deploys**: last ten, filters, commit search, cancel queued deploys,
  redeploy, commit and compare links. The list and the **Audit log** refresh
  every two minutes while the tab is visible; the top-bar Refresh or the `r`
  key updates now.
- **Manage versions**: activate a retained version (same cutover, restart and
  health path; no download, no migration, so old code must still work with the
  current database), delete retained files, change retention.
- **Rollback** is automatic on a failed restart or health after cutover, and
  manual through Manage versions.
- **`deployd check`** validates settings, state database, secrets and their
  file mode, paths, executables and warns about unlinked site paths or frozen
  apps. Run it after hand-editing configuration.
- **`/healthz`** reports `ok` only when the state database is writable and the
  worker is alive. Behind Cloudflare the access log shows the visitor's address.
- **Updating deployd** is `update-ubuntu.sh`; deployd cannot deploy itself
  through its own queue, since the restart would kill the deploy in progress.

### Database migrations

`deployd-migrate` applies `NNN_name.sql` files in numeric order, records each
file's SHA256 and refuses to run when an applied file changed. Use it as the
app's migrate command so a failed migration halts the deploy before cutover.

```bash
deployd-migrate --dir migrations --dialect mssql --dsn-env DEPLOYD_MIGRATE_DSN
deployd-migrate --dir migrations --dialect postgres --dsn-file /etc/app/dsn --dry-run
deployd-migrate --dir migrations status
```

Pass the DSN with `--dsn-env` (list that variable in the app's environment
passthrough) or `--dsn-file`; `--dsn` is visible in `ps`. SQL Server runs each
file in one transaction with `SET XACT_ABORT ON`, drains every result set so
errors after a `PRINT` still fail, and rejects files that manage their own
transactions; runners sharing a database serialize on `sp_getapplock`
(SQL Server) or an advisory lock (PostgreSQL). Install the driver with
`pip install 'deployd[mssql]'` or `'deployd[postgres]'`.

## Reference

### Request contract

```
POST /deploys
X-Deploy-Timestamp: <unix epoch seconds>        (±5 minutes)
X-Deploy-Nonce: <uuid4>                         (single use per app)
X-Deploy-Signature: sha256=<hex hmac of "{timestamp}.{nonce}.{raw body}">

{"app": "...", "commit_sha": "<40 hex>", "artifact_url": "https://...",
 "artifact_sha256": "<64 hex>", "triggered_by": "github-actions:<run_id>"}
```

`202 {deploy_id}` then `GET /deploys/{deploy_id}` reports
`queued | running | succeeded | failed | rolled_back | superseded | cancelled`
with the step timeline. Retrying the exact signed request returns the original
id. Unknown apps answer `401`, frozen apps `423`, disallowed artifact URLs `403`.

### Paths

| Path | Contents |
|---|---|
| `/opt/deployd` | git checkout, `.venv`, `.node`, `.env` (root:deployd 0640) |
| `/var/lib/deployd` | `apps.yaml`, `secrets.env` (0600), `deployd.sqlite3`, `backups/` |
| `/srv/deployd/<app>/releases/` | `current/` plus retained `<sha>-<deploy_id>/` versions |
| `/etc/deployd/site-roots` | allowed parents for website connection |
| `/etc/deployd/tls/` | origin certificate and key |
| `/var/lib/deployd-connect` | root-only website recovery data |
| `/var/lib/deployd-auth/htpasswd` | management login |

### `.env`

`DEPLOYD_ADMIN_TOKEN`, `DEPLOYD_GITHUB_TOKEN` (fallback for private repos),
`DEPLOYD_BIND_HOST` / `DEPLOYD_BIND_PORT` (`127.0.0.1:8300`, only Nginx talks
to it), `DEPLOYD_DRAIN_TIMEOUT_SECONDS` (how long a stop waits for an in-flight
cutover, default 600; systemd's stop timeout is above it), `DEPLOYD_DB_PATH`,
`DEPLOYD_APPS_CONFIG`, `DEPLOYD_SECRETS_FILE`. Per-app overrides
`DEPLOYD_SECRET_<APP>` and `DEPLOYD_GITHUB_TOKEN_<APP>` win over the UI and
cannot be changed from it. Changes to `.env` need a service restart.

### `apps.yaml`

Everything the UI edits lives in `/var/lib/deployd/apps.yaml`;
[config/apps.example.yaml](config/apps.example.yaml) documents every key,
including artifact size limits, timeouts, hooks, health assertions and
`env_passthrough`. Release storage, activation and retention are described in
[docs/release-layout.md](docs/release-layout.md).

### Windows

NSSM service, IIS physical path on a junction, `Restart-WebAppPool`:
[deploy/windows.md](deploy/windows.md) (installer script untested on real
Windows). Security properties and the hardening checklist: [SECURITY.md](SECURITY.md).

## Local development

Python 3.11.4+, uv, Node 24, pnpm 11.

```bash
make install                                   # Python + web dependencies
cp .env.example .env                           # set DEPLOYD_ADMIN_TOKEN and the local paths
cp config/apps.example.yaml config/apps.yaml
make dev                                       # API on 127.0.0.1:8300
make dev-web                                   # Vite UI, proxies /api to the API
make check                                     # ruff, pytest, oxlint, vitest
make check-audit                               # pip-audit and pnpm audit
```

```
src/deployd/        FastAPI app, worker pipeline, SQLite store, migrate and check CLIs
web/                React admin UI (Vite + Tailwind)
deploy/             Ubuntu installer/updater/uninstaller, systemd unit, root helpers, Windows guide
examples/           generated reference workflow + vendorable notifier
docs/               website connection and release layout in depth
tests/              pytest (API, worker, installer); web/ uses vitest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).
Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
