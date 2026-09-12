# shellcheck shell=bash
# Shared constants and helpers for the Ubuntu installer and uninstaller.
# Sourced, never executed; the constants are consumed by the sourcing scripts.

# shellcheck disable=SC2034
readonly REPO_ROOT="/opt/deployd"
readonly STATE_DIR="/var/lib/deployd"
readonly BACKUP_DIR="/var/lib/deployd/backups"
readonly RELEASE_ROOT="/srv/deployd"
readonly CONNECT_STATE_DIR="/var/lib/deployd-connect"
readonly SERVICE_FILE="/etc/systemd/system/deployd.service"
readonly SERVICE_DROPIN="/etc/systemd/system/deployd.service.d"
readonly NGINX_SITE="/etc/nginx/sites-available/deployd"
readonly NGINX_LINK="/etc/nginx/sites-enabled/deployd"
readonly HTPASSWD_FILE="/etc/nginx/deployd.htpasswd"
readonly HELPER_BIN="/usr/local/libexec/deployd/connect-website"
readonly HELPER_SUDOERS="/etc/sudoers.d/deployd-connect"
readonly NODE_DIR="${REPO_ROOT}/.node"
readonly TOOL_BIN_DIR="${HOME}/.local/bin"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

require_normal_linux_user() {
  local verb=$1
  [[ $(uname -s) == Linux ]] || die "this ${verb} requires Debian/Ubuntu with systemd"
  [[ ${EUID} -ne 0 ]] || die "run this ${verb} as the repository owner, without sudo"
  command -v sudo >/dev/null 2>&1 || die "sudo is required for system configuration"
}

# Answers "yes" when DEPLOYD_INSTALL_YES=1, otherwise asks; default is "no".
confirm() {
  local prompt=$1 answer
  if [[ ${DEPLOYD_INSTALL_YES:-} == 1 ]]; then
    return 0
  fi
  read -r -p "${prompt} [y/N]: " answer || return 1
  [[ $answer =~ ^([yY]|[yY][eE][sS])$ ]]
}

# Removes all but the newest $2 root-owned files matching $3 directly under $1.
# Names must sort chronologically (timestamp suffix), so no mtime dependency.
prune_privileged_files() {
  local dir=$1 keep=$2 pattern=$3 path
  sudo test -d "$dir" || return 0
  while IFS= read -r path; do
    [[ -n $path ]] && sudo rm -f -- "$path"
  done < <(sudo find "$dir" -maxdepth 1 -type f -name "$pattern" -print | sort -r | tail -n +"$((keep + 1))")
}

remove_file() {
  local path=$1
  if sudo test -e "$path" || sudo test -L "$path"; then
    sudo rm -f -- "$path"
  fi
}
