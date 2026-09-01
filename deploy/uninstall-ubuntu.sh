#!/usr/bin/env bash

set -Eeuo pipefail

readonly REPO_ROOT="/opt/deployd"
readonly STATE_DIR="/var/lib/deployd"
readonly SERVICE_FILE="/etc/systemd/system/deployd.service"
readonly SERVICE_DROPIN="/etc/systemd/system/deployd.service.d"
readonly NGINX_SITE="/etc/nginx/sites-available/deployd"
readonly NGINX_LINK="/etc/nginx/sites-enabled/deployd"
readonly HTPASSWD_FILE="/etc/nginx/deployd.htpasswd"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

require_uninstall_user() {
  [[ ${EUID} -ne 0 ]] || die "run this uninstaller as a normal user, without sudo"
  command -v sudo >/dev/null 2>&1 || die "sudo is required for system removal"
}

check_checkout() {
  local script_dir repo_root
  script_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  repo_root=$(cd -P -- "$script_dir/.." && pwd)
  [[ $repo_root == "$REPO_ROOT" ]] || die "run the uninstaller from $REPO_ROOT"
  [[ -f "$repo_root/pyproject.toml" && -f "$repo_root/deploy/deployd.service" ]] ||
    die "$REPO_ROOT does not look like a deployd checkout"
  if [[ -n $(git -C "$repo_root" status --porcelain) ]]; then
    warn "the repository has local changes; they will be included only if you create a backup"
  fi
}

show_scope() {
  printf '%s\n' \
    "This permanently removes deployd and all installer-owned data:" \
    "  $SERVICE_FILE and $SERVICE_DROPIN" \
    "  $NGINX_SITE, $NGINX_LINK, and deployd Nginx backups" \
    "  $HTPASSWD_FILE" \
    "  $STATE_DIR (database, app registry, and signing secrets)" \
    "  system user and group: deployd" \
    "  $REPO_ROOT (source, .env, dependencies, and built frontend)" \
    "" \
    "Shared packages, deployed application releases, firewall rules, DNS, and" \
    "user-created sudoers rules are not removed."
}

choose_backup() {
  local answer
  read -r -p "Create a permission-restricted backup first? [Y/n]: " answer
  [[ -z $answer || ${answer,,} == "y" || ${answer,,} == "yes" ]]
}

confirm_removal() {
  local confirmation
  read -r -p 'Type "REMOVE deployd" to continue: ' confirmation
  [[ $confirmation == "REMOVE deployd" ]] || die "uninstall cancelled"
}

create_backup() {
  local output=$1
  local -a items=()
  local item
  for item in \
    "opt/deployd" \
    "var/lib/deployd" \
    "etc/systemd/system/deployd.service" \
    "etc/systemd/system/deployd.service.d" \
    "etc/nginx/sites-available/deployd" \
    "etc/nginx/deployd.htpasswd"; do
    if sudo test -e "/$item" || sudo test -L "/$item"; then
      items+=("$item")
    fi
  done

  [[ ${#items[@]} -gt 0 ]] || die "nothing is available to back up"
  [[ ! -e $output && ! -L $output ]] || die "backup destination already exists: $output"
  (umask 077 && : >"$output")
  if ! sudo tar \
    --exclude="opt/deployd/.venv" \
    --exclude="opt/deployd/.pytest_cache" \
    --exclude="opt/deployd/.ruff_cache" \
    --exclude="opt/deployd/web/node_modules" \
    --exclude="opt/deployd/web/dist" \
    -C / -czf "$output" -- "${items[@]}"; then
    rm -f -- "$output"
    die "backup creation failed"
  fi
  sudo chown "$(id -u):$(id -g)" "$output"
  chmod 0600 "$output"
}

remove_file() {
  local path=$1
  if sudo test -e "$path" || sudo test -L "$path"; then
    sudo rm -f -- "$path"
  fi
}

remove_tree() {
  local path=$1
  local expected=$2
  [[ $path == "$expected" ]] || die "refusing unexpected removal target: $path"
  if sudo test -e "$path" || sudo test -L "$path"; then
    sudo rm -rf -- "$path"
  fi
}

remove_service() {
  sudo systemctl disable --now deployd.service >/dev/null 2>&1 || true
  remove_file "$SERVICE_FILE"
  remove_tree "$SERVICE_DROPIN" "/etc/systemd/system/deployd.service.d"
  sudo systemctl daemon-reload
  sudo systemctl reset-failed deployd.service >/dev/null 2>&1 || true
}

remove_nginx_config() {
  remove_file "$NGINX_LINK"
  remove_file "$NGINX_SITE"
  remove_file "$HTPASSWD_FILE"
  if sudo test -d /etc/nginx/sites-available; then
    sudo find /etc/nginx/sites-available -maxdepth 1 -type f \
      -name 'deployd.backup.*' -delete
  fi

  if command -v nginx >/dev/null 2>&1; then
    if sudo nginx -t; then
      if systemctl is-active --quiet nginx && ! sudo systemctl reload nginx; then
        warn "Nginx reload failed"
      fi
    else
      warn "Nginx configuration is invalid after removing deployd; Nginx was not reloaded"
    fi
  fi
}

remove_service_identity() {
  if id deployd >/dev/null 2>&1; then
    local processes
    processes=$(sudo ps -u deployd -o pid= | xargs)
    [[ -z $processes ]] || die "deployd still owns running processes: $processes"
    sudo userdel deployd
  fi
  if getent group deployd >/dev/null 2>&1; then
    sudo groupdel deployd
  fi
}

main() {
  require_uninstall_user
  check_checkout
  show_scope

  local backup_path=""
  if choose_backup; then
    backup_path="$HOME/deployd-uninstall-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  fi
  confirm_removal
  sudo -v

  local service_was_active="false"
  if [[ -n $backup_path ]]; then
    if systemctl is-active --quiet deployd.service; then
      service_was_active="true"
      sudo systemctl stop deployd.service
    fi
    if ! (create_backup "$backup_path"); then
      if [[ $service_was_active == "true" ]]; then
        sudo systemctl start deployd.service || true
      fi
      die "uninstall cancelled because the backup failed"
    fi
    printf 'Backup created: %s\n' "$backup_path"
  fi

  remove_service
  remove_nginx_config
  remove_tree "$STATE_DIR" "/var/lib/deployd"
  remove_service_identity

  cd /
  remove_tree "$REPO_ROOT" "/opt/deployd"

  printf '%s\n' \
    "deployd has been removed." \
    "Shared packages and external application releases were preserved."
  if [[ -n $backup_path ]]; then
    printf 'Backup retained at: %s\n' "$backup_path"
  fi
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
