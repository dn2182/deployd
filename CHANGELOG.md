# Changelog

## 0.1.0 — unreleased

Initial release.

### Hardening pass (September 2026)

- The last-deploy summary excludes cleanup and other local operations. History
  pagination ignores stale responses after filter, session or refresh changes.
- Release cleanup shares the per-app deployment queue and drains safely on
  shutdown. History is retained so old saved versions remain activatable.
- `deployd check` only inspects configuration, paths and release metadata; it
  never creates/migrates state or repairs directories while the service runs.
- Keep manual refresh and explicit version selection; remove fast polling,
  history rollback shortcuts, outbound webhook notifications and the untested
  Windows installer/advisory CI job.
- Migrate, restart and hook subprocesses run with a scrubbed environment; the
  admin token, GitHub tokens and pinned signing secrets loaded from `.env` no
  longer reach code shipped in an artifact.
- `GET /deploys/{id}` returns step output only to requests carrying the admin
  token; CI polling sees step names and statuses.
- Unknown apps and unconfigured secrets answer `401` like a bad signature.
  Nonces are scoped per app. Timestamp, nonce and signature headers are
  validated by shape before use; non-ASCII admin tokens are rejected cleanly.
- Worker tasks are supervised and respawned after a crash. Steps from
  `migrate` onward run shielded; shutdown drains them for up to
  `DEPLOYD_DRAIN_TIMEOUT_SECONDS`, and work interrupted earlier returns to the
  queue and resumes on restart.
- Restart scripts that leave a daemon holding stdout no longer hang the deploy
  or get their app killed on timeout.
- Artifacts are hashed while streaming; extraction, pruning and cleanup run
  off the event loop; downloads retry transient errors and check free disk
  space; ZIP executables keep their mode bits.
- The SQLite schema is versioned with `PRAGMA user_version`. Version 2 adds
  the deploy `kind` column, an index on `deploy_steps` and the `audit_log`
  table; older databases upgrade in place.
- New: a newer queued push supersedes older queued ones (`superseded`),
  queued deploys can be cancelled (`cancelled`), apps can be frozen (`423`),
  optional `before_cutover` and `after_health` hooks, health assertions on
  body or header with a `{commit_sha}` placeholder, per-command timeouts, an audit log
  of admin actions with the proxy user, `GET /admin/apps/{name}/status`,
  history filtering and pagination, `deployd check`, hourly maintenance
  (nonce expiry), `.incoming` sweep at startup, `/healthz`
  reporting database and worker state, and systemd watchdog support.
- `requires-python` is now `>=3.11.4` (tar data filter).

- Searchable, paginated application navigation with one selected app, exclusive
  website/version/GitHub tabs, and explicit refresh instead of background polling.
- Safely reconnect restored websites using their receipts, retain restored-file
  edits across cycles, and keep the original baseline. Diagnose unsafe website
  parent permissions during install without changing shared permissions.

- Ask whether to restore current website files to their original paths or retain
  web-root symlinks when removing apps and uninstalling. Use verified staged copies,
  atomic replacement and retryable recovery; keep release backups and stop removal
  on restoration failures.

- Confirmed website connection in the management UI, backed by an optional,
  root-owned installer helper restricted to direct `/var/www` children. Preserve
  the original site as `b4deployd`, support local activation, and exclude it from
  automatic retention. Connection journals and live sites survive uninstall.

- Normalize newly extracted POSIX release directories to `0755` before cutover,
  including implicit archive directories, without relaxing file permissions,
  private staging, release metadata, or the service umask.

- Deploy API: HMAC-authenticated `POST /deploys` (timestamp + signed nonce +
  body, atomic replay protection), `GET /deploys/{id}` status with per-step log.
- Worker: per-app serialized pipeline — download, SHA256 verify, unpack
  (zip/tar, traversal-safe), migrate, atomic cutover (symlink/junction),
  restart, health check, auto-rollback, release pruning, crash recovery, and
  bounded downloads/extraction/command output.
- `deployd-migrate`: forward-only SQL migrations with checksummed immutable
  files, versioned table, `GO` batch support (SQL Server via pyodbc).
- Admin API + React UI: app registry CRUD, secret rotation (shown once),
  deploy history with step drill-down, redeploy, and manual status refresh.
- Guided bilingual application setup for repositories, signing secrets, per-app
  private GitHub credentials, local site paths, health checks, and retention.
  Internal paths are automatic and read-only; no JSON editor is exposed.
- Optional HTTP health checks with explicit skipped status; static entry-file
  validation and command-failure rollback remain enabled.
- Local version activation and confirmed cleanup, with optional per-app retention
  of a chosen number of previous versions and active-version protection.
- Standard real-current directory layout with native atomic exchange,
  rollback, fixed external web-root symlinks, and interrupted-swap recovery.
- Windows support: junction cutover, NSSM/IIS guide.
- Installer ownership repair and runtime preflight, safe service stopping,
  guarded uninstall with backup and Nginx rollback, and Ubuntu lifecycle CI tests.
- Examples: GitHub Actions reference workflow, vendorable notify script.
