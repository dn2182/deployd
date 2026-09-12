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
  [[ $(uname -s) == Linux ]] || die "this uninstaller requires Debian/Ubuntu with systemd"
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
    "  system user and group: deployd (only when no app data is retained)" \
    "  $REPO_ROOT (source, .env, dependencies, and built frontend)" \
    "" \
    "Shared packages, deployed application releases, firewall rules, DNS, and" \
    "user-created sudoers rules are not removed." \
    "Custom runtime paths outside /opt/deployd and /var/lib/deployd are not backed up or removed."
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
    "etc/nginx/sites-enabled/deployd" \
    "etc/nginx/deployd.htpasswd"; do
    if sudo test -e "/$item" || sudo test -L "/$item"; then
      items+=("$item")
    fi
  done
  while IFS= read -r -d '' item; do
    items+=("${item#/}")
  done < <(sudo find /etc/nginx/sites-available -maxdepth 1 -type f -name 'deployd.backup.*' -print0)

  [[ ${#items[@]} -gt 0 ]] || die "nothing is available to back up"
  [[ ! -e $output && ! -L $output ]] || die "backup destination already exists: $output"
  (umask 077 && set -o noclobber && : >"$output") || die "could not create backup file"
  # The normal user opens the archive; privileged tar never follows an output symlink.
  # shellcheck disable=SC2024
  if ! sudo tar \
    --exclude="opt/deployd/.python" \
    --exclude="opt/deployd/.venv" \
    --exclude="opt/deployd/.pytest_cache" \
    --exclude="opt/deployd/.ruff_cache" \
    --exclude="opt/deployd/web/node_modules" \
    --exclude="opt/deployd/web/dist" \
    -C / -czf - -- "${items[@]}" >"$output"; then
    rm -f -- "$output"
    die "backup creation failed"
  fi
  chmod 0600 "$output" || die "could not protect backup"
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
  if sudo test -e "$SERVICE_FILE"; then
    sudo systemctl disable deployd.service
  fi
  remove_file "$SERVICE_FILE"
  remove_tree "$SERVICE_DROPIN" "/etc/systemd/system/deployd.service.d"
  sudo systemctl daemon-reload
  sudo systemctl reset-failed deployd.service >/dev/null 2>&1 || true
}

remove_nginx_config() {
  local backup
  backup=$(mktemp -d /tmp/deployd-uninstall-nginx.XXXXXX)
  local path
  for path in "$NGINX_SITE" "$NGINX_LINK" "$HTPASSWD_FILE"; do
    if sudo test -e "$path" || sudo test -L "$path"; then
      sudo cp -a --parents "$path" "$backup"
    fi
  done
  remove_file "$NGINX_LINK"
  remove_file "$NGINX_SITE"
  remove_file "$HTPASSWD_FILE"
  if command -v nginx >/dev/null 2>&1; then
    if ! sudo nginx -t || { systemctl is-active --quiet nginx && ! sudo systemctl reload nginx; }; then
      for path in "$NGINX_SITE" "$NGINX_LINK" "$HTPASSWD_FILE"; do
        if sudo test -e "$backup$path" || sudo test -L "$backup$path"; then
          sudo cp -a "$backup$path" "$path"
        fi
      done
      sudo systemctl reload nginx || true
      die "Nginx removal failed; configuration restored from $backup; uninstall stopped"
    fi
  fi
  if sudo test -d /etc/nginx/sites-available; then
    sudo find /etc/nginx/sites-available -maxdepth 1 -type f \
      -name 'deployd.backup.*' -delete
  fi
  sudo rm -rf -- "$backup"
}

remove_service_identity() {
  if [[ $preserve_identity == "true" ]] || sudo test -d /srv/deployd; then
    printf 'Keeping the deployd account to preserve ownership of retained app data.\n'
    return
  fi
  if id deployd >/dev/null 2>&1; then
    sudo userdel deployd
  fi
  if getent group deployd >/dev/null 2>&1; then
    sudo groupdel deployd
  fi
}

stop_service_safely() {
  if sudo test -e "$SERVICE_FILE" || systemctl is-active --quiet deployd.service; then
    sudo systemctl stop deployd.service || die "could not stop deployd; nothing was removed"
  fi
  if systemctl is-active --quiet deployd.service; then
    die "deployd is still running; nothing was removed"
  fi
  if id deployd >/dev/null 2>&1; then
    local processes
    processes=$(sudo pgrep -u deployd || [[ $? == 1 ]]) || die "could not check deployd processes"
    [[ -z $processes ]] || die "deployd still owns running processes; nothing was removed"
  fi
}

check_removal_targets() {
  local path mounts target
  mounts=$(findmnt -rn -o TARGET) || die "could not inspect mounts before removal"
  for path in "$REPO_ROOT" "$STATE_DIR" "$SERVICE_FILE" "$SERVICE_DROPIN" "$NGINX_SITE" "$HTPASSWD_FILE"; do
    sudo test ! -L "$path" || die "refusing symbolic-link removal target: $path"
    while IFS= read -r target; do
      case "$target" in
        "$path"|"$path"/*) die "refusing removal target containing a mount: $path" ;;
      esac
    done <<<"$mounts"
  done
  if sudo test -L "$NGINX_LINK"; then
    [[ $(readlink -f "$NGINX_LINK") == "$NGINX_SITE" ]] || die "Nginx link belongs to another site"
  elif sudo test -e "$NGINX_LINK"; then
    die "Nginx enabled site is not the installer-managed symlink"
  fi
}

main() {
  require_uninstall_user
  check_checkout
  cd "$REPO_ROOT"
  show_scope

  local backup_path=""
  if choose_backup; then
    backup_path="$HOME/deployd-uninstall-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    backup_path=$(realpath -m "$backup_path")
    case "$backup_path" in
      "$REPO_ROOT"/*|"$STATE_DIR"/*) die "backup must be outside the directories being removed" ;;
    esac
  fi
  confirm_removal
  sudo -v
  check_removal_targets

  local service_was_active="false" preserve_identity="true"
  if systemctl is-active --quiet deployd.service; then
    service_was_active="true"
    trap 'if [[ $? -ne 0 ]] && sudo test -f /etc/systemd/system/deployd.service; then sudo systemctl start deployd.service || true; fi' EXIT
  fi
  stop_service_safely
  if sudo "$REPO_ROOT/.venv/bin/python" -c \
    'from deployd.config import get_app_registry; raise SystemExit(bool(get_app_registry()))' \
    2>/dev/null; then
    preserve_identity="false"
  fi
  if [[ -n $backup_path ]]; then
    if ! (create_backup "$backup_path"); then
      if [[ $service_was_active == "true" ]]; then
        sudo systemctl start deployd.service || true
      fi
      die "uninstall cancelled because the backup failed"
    fi
    printf 'Backup created: %s\n' "$backup_path"
  fi

  remove_nginx_config
  remove_service
  remove_tree "$STATE_DIR" "/var/lib/deployd"
  sudo rmdir /srv/deployd 2>/dev/null || true
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
