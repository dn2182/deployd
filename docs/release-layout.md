# Release layout and retention

How deployd stores releases on disk, how activation and cleanup work, and the permission rules for extracted files.

## Version management

Open **Manage versions** on an application to view its local releases, activate
an older version, or delete an unneeded version's files with confirmation.
Activation uses the same per-app queue, restarts the app, and checks health.
It does not download artifacts or run migrations; older code must still work
with the current database. A failed activation attempts to restore the prior
version. Interrupted activations are marked failed, not replayed on restart;
inspect the active version before retrying.

## Real current folder

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
can be connected from **Website connection → Connect website** after the first
release is ready. Saving an app never switches its live website.
