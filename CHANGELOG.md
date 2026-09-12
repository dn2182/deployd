# Changelog

## 0.1.0 — unreleased

Initial release.

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
