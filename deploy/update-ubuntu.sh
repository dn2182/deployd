#!/usr/bin/env bash
# Pull the latest release and upgrade in place, keeping every installed setting.
set -Eeuo pipefail

repo_root=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
git -C "$repo_root" pull --ff-only
exec "$repo_root/deploy/install-ubuntu.sh" --update
