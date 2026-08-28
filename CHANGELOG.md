# Changelog

## 0.1.0 — unreleased

Initial release.

- Deploy API: HMAC-authenticated `POST /deploys` (timestamp window, nonce
  replay protection), `GET /deploys/{id}` status with per-step log.
- Worker: per-app serialized pipeline — download, SHA256 verify, unpack
  (zip/tar, traversal-safe), migrate, atomic cutover (symlink/junction),
  restart, health check, auto-rollback, release pruning.
- `deployd-migrate`: forward-only SQL migrations with checksummed immutable
  files, versioned table, `GO` batch support (SQL Server via pyodbc).
- Admin API + React UI: app registry CRUD, secret rotation (shown once),
  deploy history with step drill-down, redeploy, live polling.
- Windows support: junction cutover, NSSM/IIS guide.
- Examples: GitHub Actions reference workflow, vendorable notify script.
