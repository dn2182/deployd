# Security Policy

deployd is a deploy agent — it runs commands on your server by design. Read
this before exposing it anywhere.

## Reporting a vulnerability

Please open a [private security advisory](../../security/advisories/new) on
GitHub. Do not open public issues for vulnerabilities. You should get a
response within a few days.

## Deployment hardening checklist

- Bind to `127.0.0.1` and expose only through a reverse proxy / Cloudflare;
  never expose the port directly.
- `/admin` has its own token auth and is disabled until `DEPLOYD_ADMIN_TOKEN`
  is set. Keep it reachable only from localhost, a VPN/tailnet, or behind
  Cloudflare Access.
- One HMAC secret per app; rotate via the admin API. Secrets live in env vars
  or `config/secrets.env` (0600) — never in git.
- Keep `allowed_url_prefix` tight: it is the only place artifacts may be
  downloaded from, even with a valid signature.
- Run the service as a dedicated low-privilege user; grant restart rights per
  app (narrow sudoers rule on Linux, IIS permissions on Windows).

## Security properties you can rely on

- Deploy requests are authenticated with HMAC-SHA256 over `timestamp.body`,
  a ±5 minute timestamp window, a persisted nonce store (replay protection),
  and constant-time comparison.
- Artifacts are verified against the signed SHA256 before they are unpacked.
- Archive extraction rejects path traversal (zip checked per entry; tar via
  the `data` filter).
- Deploys are pinned to a commit SHA, never a branch name.
