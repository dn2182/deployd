# Changelog

## 0.1.0 — unreleased

Initial release.

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
  deploy history with step drill-down, redeploy, live polling.
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
