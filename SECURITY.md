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
- Website removal/restoration uses the same restricted helper. It stages a bounded
  copy of the current release and exchanges only the expected web-root symlink;
  unrelated paths are not overwritten. Stop external file writers first. Restored
  files remain owned by deployd, so uninstall retains that account and release data.
- Enforce request/body and rate limits at the reverse proxy as an additional
  public-edge control; deployd also limits the signed request body itself.
- Migration, restart and hook commands run with a scrubbed environment (PATH,
  HOME, locale and temp variables, plus `DEPLOYD_APP`, `DEPLOYD_DEPLOY_ID`,
  `DEPLOYD_COMMIT_SHA`, `DEPLOYD_RELEASE_DIR`, `DEPLOYD_CURRENT`, and the
  service variables an app lists in `env_passthrough`). The admin token, GitHub
  tokens and pinned signing secrets loaded from `.env` never reach code shipped
  in an artifact; `env_passthrough` refuses those names. The systemd unit uses
  `KillMode=mixed` so a stop lets in-flight commands finish during the drain. Do not wrap commands in scripts that re-source
  the service environment.
- `GET /deploys/{id}` is unauthenticated for CI polling and returns step names
  and statuses only; command output is included only when the request carries
  a valid `X-Admin-Token`. Deploy ids printed in CI logs therefore reveal
  nothing beyond the step timeline.
- Unknown apps and apps without a configured secret answer `401` exactly like a
  bad signature, so the public endpoint does not enumerate the registry; the
  real reason is in the service log.
- Every admin mutation is written to the audit log with the actor: the Basic
  Auth user forwarded by the reverse proxy in `X-Remote-User`, or `admin-token`
  for direct callers. Only the proxy should be able to set that header.
- Run `deployd check` after changing configuration; it verifies secrets, paths,
  executables and file modes without starting the service.

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
- Nonces are scoped per app, so one app's replay window never interacts with
  another's.
- Health checks can assert a body substring or header value containing the
  deployed SHA, so a restart that silently left the old process running fails
  the deploy and triggers a rollback instead of passing.
- Steps from `migrate` onward finish even while the service is stopping; the
  process waits up to `DEPLOYD_DRAIN_TIMEOUT_SECONDS` for them. Work
  interrupted before that point returns to the queue and resumes on restart.

## Known limitations

- The private-network check on artifact URLs resolves the hostname once and
  then lets the HTTP client resolve it again, so a host under attacker DNS
  control could answer differently on the second lookup. With the default
  allowlist pinned to `api.github.com` and its release CDN this is not
  exploitable; treat `allowed_url_prefix` on a self-hosted artifact server as
  trusting that server's DNS.
- The Windows junction swap has a brief window with no `current` link, and no
  atomic directory exchange exists there.

## Threat-model boundary

deployd prevents a workflow from changing the configured deployment commands,
target paths, or application identity. It cannot make a compromised build
artifact trustworthy: code inside that artifact runs with the deployed
application's privileges. Keep CI dependencies pinned, protect production
environments, and run every application under a dedicated least-privileged
identity.
