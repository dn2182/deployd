# Website connection in depth

Reference for the root helper that links an existing static site to deployd's `releases/current`. The README covers the normal flow; this page keeps the recovery and permission details.

## Connect an existing website

Rerun `bash deploy/install-ubuntu.sh` as your normal user and opt into the
website connection helper. It installs a root-owned, isolated Python helper
and one restricted sudo rule. This authorizes deployd to connect direct children
of `/var/www`, not arbitrary paths or shell commands. Upgrades preserve this opt-in.
The installer checks that `/var/www` is root-owned and not group/world-writable.
It reports unsafe permissions without changing them. For a shared `root:services`
parent with mode `2775`, the administrator can review `sudo chmod go-w /var/www`:
this keeps its group and setgid bit, but restricts creating/renaming direct children.
Do not apply permission changes recursively to the websites.

After a successful deployment, open the app's **Website connection**, check
the paths, and type its name to confirm the live switch. The existing directory
is exchanged atomically with a fixed symlink to `releases/current`. Its original
files become `releases/b4deployd`, available through **Manage versions → Activate**.
Nginx configuration, including API proxy locations, is unchanged. No reload is needed
for an unchanged Nginx document-root path.

If the site path is later replaced by a real folder or another link (a manual
copy, a stray config), the Website tab reports it as not connected and offers
**Fix connection**: the replacement moves into `/var/lib/deployd-connect`
(nothing is deleted) and the path becomes a symlink to `current` again. The
status card shows the site path state, every deploy logs a `website` step that
warns when the path is not linked, and `deployd check` warns as well.

`b4deployd` is excluded from automatic cleanup and does not count against the
normal version limit. You can delete it explicitly when it is neither active nor
a protected previous version. Already-connected symlinks are left alone; the
helper cannot reconstruct an original site that was moved manually.

Automatic connection requires Linux, managed directory layout, an existing static
site directly under an allowed site root (`/var/www`, plus any directory listed
in root-owned `/etc/deployd/site-roots`, which the installer creates and checks;
`DEPLOYD_INSTALL_SITE_ROOTS=/srv/sites:/opt/www` adds roots unattended), and
website/releases/helper storage on the same
filesystem. The original must contain only ordinary files and directories (no
symlinks, hardlinks, or special files), with public-read files and traversable
directories. The limit is 2 GiB and 100,000 entries. Connection makes backup
directories deployd-owned and `0755`; regular-file ownership and modes are preserved.
Pause any process writing to the old site before connecting it. This is for static
deployment output, not uploads, secrets, or a running application data directory.

Interrupted switches preserve the original either in `releases/b4deployd` or in
root-only `/var/lib/deployd-connect/<app>/original`. Retry **Connect website** to
resume; unexpected path changes or damaged metadata require operator inspection.
Never delete helper recovery storage while recovering. Uninstall removes the
helper and its sudo rule but preserves releases and recovery data. It asks whether
to restore website paths as real folders or keep their symlinks.

## Remove an app or uninstall without leaving its web-root symlink

Removing a website app asks you to choose **Restore current live files as a real
folder** or **Keep the website symlink connected**, and to type the app name.
Restore copies the current version into private staging, verifies it, and atomically
replaces only that app's fixed `/var/www/<site>` link with the real directory.
It does **not** switch to `b4deployd`. The app is unregistered only after restoration
succeeds; new deployments are rejected while removal is queued or running.

The uninstaller also asks **restore / keep / cancel** when it finds standard
website links under `/var/www`, including links for apps previously unregistered
with **Keep**. It stops deployd and creates the requested backup first, restores
the selected sites before deleting service/configuration files, and stops if any
restore fails. Sites restored before a later failure remain safely restored.

Run the updated installer first so the root-owned helper supports restoration.
Stop other processes writing website files. Same-mount and static-file checks
still apply, and staging needs enough free space for another copy of the live site.
Unrelated links and custom paths outside `/var/www` are not changed; handle those
manually. Current and retained releases, `b4deployd`, recovery journals, and the
deployd account are preserved. Restored folders/files are deployd-owned; this is
path restoration, not recovery of historical Unix ownership. Nginx routes are unchanged.

Re-adding the same app and local path allows a confirmed reconnect when its real
folder matches deployd's restore receipt. The restored folder (including edits)
is retained under `/var/lib/deployd-connect/<app>/restore-<site>/reconnected-files`;
on the next removal this recovery directory moves to `saved-<timestamp>`. These
root-only recovery copies are retained for manual recovery, not automatic cleanup
or UI version activation. `b4deployd` remains unchanged. Replaced folders or missing
receipts require administrator inspection instead of an automatic live switch.

The Ubuntu installer creates `/srv/deployd` owned by `deployd`. Existing
installations upgrading without rerunning the installer need this once:

```bash
sudo install -d -o deployd -g deployd -m 0755 /srv/deployd
```

Custom roots must also be writable by the service. On POSIX systems, newly
extracted release directories (including the release root) are set to `0755`
before publication. The service's `UMask=0077`, private staging parent, and file
permissions are not relaxed. For static sites, use a tar artifact with files
mode `0644` and verify Nginx-user readability; ZIP file extraction still follows
the service umask. Never package secrets in a publicly served release.
This applies to new extractions, not existing or retained releases. Previously
deployed directories with restrictive permissions need a scoped repair before
activation; restarting deployd does not rewrite their permissions.

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
