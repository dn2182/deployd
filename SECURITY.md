# Security Policy

deployd is a deploy agent — it runs commands on your server by design. Read
this before exposing it anywhere.

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/dn2182/deployd/security/advisories/new)
on GitHub. Do not open public issues for vulnerabilities. You should get a
response within a few days.

## Deployment hardening checklist

- Bind to `127.0.0.1` and expose only through a reverse proxy / Cloudflare;
  never expose the port directly.
- `/admin` has its own token auth and is disabled until `DEPLOYD_ADMIN_TOKEN`
  is set. Keep it reachable only from localhost, a VPN/tailnet, or behind
  Cloudflare Access. Treat that token as command-execution authority because
  administrators can configure migration and restart commands.
- The testing installer serves management on a separate Basic Auth port. Its
  default all-interface bind is safe only when the host firewall restricts that
  port; choose `127.0.0.1` when access through an SSH tunnel is sufficient.
- Use HTTPS or an SSH tunnel when entering credentials in management. A firewall
  allowlist or HTTP Basic Auth alone does not encrypt the connection.
- Prefer per-app GitHub tokens limited to **Contents: read** on the specific
  repository. The UI stores them in `DEPLOYD_SECRETS_FILE` (0600), not app YAML;
  API responses and validation errors do not include their values. The global
  `DEPLOYD_GITHUB_TOKEN` remains a fallback if no app token exists. Tokens are sent
  only to the initial HTTPS GitHub release-asset API URL, never to redirects.
- One HMAC secret per app; rotate via the admin API. Secrets live in env vars
  or `config/secrets.env` (0600) — never in git. Admin and app secrets must
  contain at least 32 bytes.
- Keep `allowed_url_prefix` tight and list only required
  `allowed_redirect_hosts`. Private/non-routable destinations are blocked by
  default, and download/extraction quotas should stay close to real artifact
  sizes.
- Run the service as a dedicated low-privilege user; grant restart rights per
  app (narrow sudoers rule on Linux, IIS permissions on Windows).
- The optional website connector grants the deployd account authority over
  existing static document roots directly under `/var/www`. Enable it only if
  that scope is appropriate for this server. The helper is root-owned, runs the
  system Python in isolated mode, accepts no arbitrary paths or commands, and
  rejects symlinks and mount crossings during traversal. UI confirmation prevents
  accidental switches; it is not a security boundary against a compromised admin
  token or service account. Keep `/var/lib/deployd-connect` root-only and retain
  its recovery data until any interrupted connection has been resolved.
- Enforce request/body and rate limits at the reverse proxy as an additional
  public-edge control; deployd also limits the signed request body itself.

## Security properties you can rely on

- Deploy requests are authenticated with HMAC-SHA256 over
  `timestamp.nonce.body`, a ±5 minute timestamp window, atomic persisted
  nonce consumption, idempotent exact retries, and constant-time comparison.
- Artifacts are verified against the signed SHA256 before they are unpacked.
- Archive extraction rejects path traversal (zip checked per entry; tar via
  the `data` filter), links/special files, and configured size/file-count
  limits.
- Artifact deploys are pinned to a commit SHA, never a branch name. Imported
  `b4deployd` files are a local recovery snapshot, not a Git-verified artifact.

## Threat-model boundary

deployd prevents a workflow from changing the configured deployment commands,
target paths, or application identity. It cannot make a compromised build
artifact trustworthy: code inside that artifact runs with the deployed
application's privileges. Keep CI dependencies pinned, protect production
environments, and run every application under a dedicated least-privileged
identity.
