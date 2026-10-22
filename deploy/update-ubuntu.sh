#!/usr/bin/env bash
# Pull the latest release and upgrade in place, keeping every installed setting.
# The checkout is managed by git, not edited on the server: any drift in tracked
# files is printed and discarded so the pull can never be blocked by it.
set -Eeuo pipefail

repo_root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
drift=$(git -C "$repo_root" status --porcelain --untracked-files=no)
if [[ -n $drift ]]; then
  printf 'Discarding local changes to tracked files:\n%s\n' "$drift" >&2
  git -C "$repo_root" diff --stat >&2 || true
  git -C "$repo_root" checkout -- .
fi
git -C "$repo_root" pull --ff-only
exec "$repo_root/deploy/install-ubuntu.sh" --update
