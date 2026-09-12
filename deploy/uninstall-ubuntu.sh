#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

purge="false"

usage() {
  printf 'usage: %s [--purge]\n' "${BASH_SOURCE[0]}"
  printf '  --purge  also remove %s, %s, installer-owned tooling and archived journal logs\n' \
    "$CONNECT_STATE_DIR" "$RELEASE_ROOT"
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --purge) purge="true" ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        usage >&2
        die "unknown argument: $arg"
        ;;
    esac
  done
}

require_uninstall_user() {
  require_normal_linux_user uninstaller
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
    "  $STATE_DIR (database, backups, app registry, and signing secrets)" \
    "  system user and group: deployd (only when no app data is retained)" \
    "  $REPO_ROOT (source, .env, dependencies, tooling, and built frontend)" \
    "  website connection helper and its installer-owned sudo rule" \
    "" \
    "Shared packages, deployed application releases, firewall rules, DNS, and" \
    "user-created sudoers rules are not removed." \
    "You will choose whether to restore connected websites to real folders or keep their symlinks." \
    "Custom runtime paths outside $REPO_ROOT and $STATE_DIR are not backed up or removed."
  if [[ $purge == "true" ]]; then
    printf '%s\n' \
      "" \
      "--purge additionally removes:" \
      "  $RELEASE_ROOT (all managed releases and b4deployd versions; not included in the backup)" \
      "  $CONNECT_STATE_DIR (website connection recovery data)" \
      "  uv, uvx and pnpm from $TOOL_BIN_DIR with their caches under $HOME/.cache" \
      "  archived journal logs for every unit on this host (journalctl --rotate and --vacuum-time=1s)"
  else
    printf '%s\n' \
      "Releases, b4deployd versions and $CONNECT_STATE_DIR recovery data are preserved (see --purge)."
  fi
}

choose_backup() {
  local answer
  read -r -p "Create a permission-restricted backup first? [Y/n]: " answer
  [[ -z $answer || $answer =~ ^([yY]|[yY][eE][sS])$ ]]
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
    "${REPO_ROOT#/}" \
    "${STATE_DIR#/}" \
    "${CONNECT_STATE_DIR#/}" \
    "${HELPER_SUDOERS#/}" \
    "${HELPER_BIN#/}" \
    "${SERVICE_FILE#/}" \
    "${SERVICE_DROPIN#/}" \
    "${NGINX_SITE#/}" \
    "${NGINX_LINK#/}" \
    "${HTPASSWD_FILE#/}"; do
    if sudo test -e "/$item" || sudo test -L "/$item"; then
      items+=("$item")
    fi
  done
  while IFS= read -r -d '' item; do
    items+=("${item#/}")
  done < <(sudo find "$(dirname -- "$NGINX_SITE")" -maxdepth 1 -type f -name 'deployd.backup.*' -print0)

  [[ ${#items[@]} -gt 0 ]] || die "nothing is available to back up"
  [[ ! -e $output && ! -L $output ]] || die "backup destination already exists: $output"
  (umask 077 && set -o noclobber && : >"$output") || die "could not create backup file"
  # The normal user opens the archive; privileged tar never follows an output symlink.
  # shellcheck disable=SC2024
  if ! sudo tar \
    --exclude="${REPO_ROOT#/}/.python" \
    --exclude="${REPO_ROOT#/}/.node" \
    --exclude="${REPO_ROOT#/}/.venv" \
    --exclude="${REPO_ROOT#/}/.pytest_cache" \
    --exclude="${REPO_ROOT#/}/.ruff_cache" \
    --exclude="${REPO_ROOT#/}/web/node_modules" \
    --exclude="${REPO_ROOT#/}/web/dist" \
    -C / -czf - -- "${items[@]}" >"$output"; then
    rm -f -- "$output"
    die "backup creation failed"
  fi
  chmod 0600 "$output" || die "could not protect backup"
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

restore_nginx_files() {
  local backup=$1 path
  for path in "$NGINX_SITE" "$NGINX_LINK" "$HTPASSWD_FILE"; do
    if sudo test -e "$backup$path" || sudo test -L "$backup$path"; then
      sudo cp -a "$backup$path" "$path"
    fi
  done
}

# A configuration that was already broken by an unrelated site must not block removal.
remove_nginx_config() {
  local backup valid_before="true" path
  backup=$(mktemp -d /tmp/deployd-uninstall-nginx.XXXXXX)
  for path in "$NGINX_SITE" "$NGINX_LINK" "$HTPASSWD_FILE"; do
    if sudo test -e "$path" || sudo test -L "$path"; then
      sudo cp -a --parents "$path" "$backup"
    fi
  done
  if command -v nginx >/dev/null 2>&1 && ! sudo nginx -t >/dev/null 2>&1; then
    valid_before="false"
  fi
  remove_file "$NGINX_LINK"
  remove_file "$NGINX_SITE"
  remove_file "$HTPASSWD_FILE"
  if command -v nginx >/dev/null 2>&1; then
    if sudo nginx -t >/dev/null 2>&1; then
      if systemctl is-active --quiet nginx && ! sudo systemctl reload nginx; then
        restore_nginx_files "$backup"
        sudo systemctl reload nginx || true
        die "Nginx reload failed; configuration restored from $backup; uninstall stopped"
      fi
    elif [[ $valid_before == "true" ]]; then
      restore_nginx_files "$backup"
      sudo systemctl reload nginx || true
      die "Nginx rejected the configuration after removal; restored from $backup; uninstall stopped"
    else
      warn "nginx -t was already failing before removal (another site); deployd files removed, nginx not reloaded"
    fi
  fi
  if sudo test -d "$(dirname -- "$NGINX_SITE")"; then
    sudo find "$(dirname -- "$NGINX_SITE")" -maxdepth 1 -type f \
      -name 'deployd.backup.*' -delete
  fi
  sudo rm -rf -- "$backup"
}

# True when apps.yaml declares at least one app; reads the file as data only.
apps_configured() {
  sudo test -f "$STATE_DIR/apps.yaml" || return 1
  sudo grep -qE '^[[:space:]]+[^[:space:]#]' "$STATE_DIR/apps.yaml"
}

remove_service_identity() {
  if [[ $preserve_identity == "true" ]] || sudo test -d "$RELEASE_ROOT" || sudo test -d "$CONNECT_STATE_DIR"; then
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
  local -a targets=("$REPO_ROOT" "$STATE_DIR" "$SERVICE_FILE" "$SERVICE_DROPIN" "$NGINX_SITE" "$HTPASSWD_FILE")
  if [[ $purge == "true" ]]; then
    targets+=("$RELEASE_ROOT" "$CONNECT_STATE_DIR")
  fi
  mounts=$(findmnt -rn -o TARGET) || die "could not inspect mounts before removal"
  for path in "${targets[@]}"; do
    sudo test ! -L "$path" || die "refusing symbolic-link removal target: $path"
    while IFS= read -r target; do
      case "$target" in
        "$path" | "$path"/*) die "refusing removal target containing a mount: $path" ;;
      esac
    done <<<"$mounts"
  done
  if sudo test -L "$NGINX_LINK"; then
    [[ $(readlink -f "$NGINX_LINK") == "$NGINX_SITE" ]] || die "Nginx link belongs to another site"
  elif sudo test -e "$NGINX_LINK"; then
    die "Nginx enabled site is not the installer-managed symlink"
  fi
}

list_website_links() {
  /usr/bin/python3 -I -c '
import os, re
from pathlib import Path
root = Path("/var/www")
if root.exists():
    for path in sorted(root.iterdir()):
        if not path.is_symlink():
            continue
        match = re.fullmatch(r"/srv/deployd/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)/releases/current", os.readlink(path))
        if match:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", path.name):
                raise SystemExit("unsupported connected website name; restore it manually first")
            print(match[1] + "\t" + path.name)
'
}

restore_websites() {
  local connections app site answer
  connections=$(list_website_links) || die "could not inspect connected websites; uninstall stopped"
  [[ -n $connections ]] || return 0
  printf 'Connected websites (including sites whose app was already removed):\n'
  while IFS=$'\t' read -r app site; do
    printf '  /var/www/%s -> %s/%s/releases/current\n' "$site" "$RELEASE_ROOT" "$app"
  done <<<"$connections"
  printf '%s\n' \
    'Restore copies the CURRENT live files back to each original website path and removes its symlink.' \
    'It does not roll back to b4deployd. Retained releases are kept. Stop external file writers first.' \
    'Keep leaves the symlinks and their /srv/deployd targets in place. Custom paths need manual handling.'
  read -r -p 'Website paths: [r]estore real folders, [k]eep symlinks, or [c]ancel: ' answer || die "no website choice; uninstall cancelled"
  case "$answer" in
    k | K | keep | KEEP)
      printf 'Keeping website symlinks and their release files.\n'
      return 0
      ;;
    r | R | restore | RESTORE) ;;
    *) die "uninstall cancelled; website paths were not changed" ;;
  esac
  sudo test -x "$HELPER_BIN" || die "run the installer and enable the updated website helper before restoring; uninstall stopped"
  while IFS=$'\t' read -r app site; do
    if ! sudo "$HELPER_BIN" detach "$app" "$site"; then
      die "website restoration failed; uninstall stopped and remaining sites were not changed. Any previously restored sites are safe. Update the helper or inspect recovery storage before retrying"
    fi
  done <<<"$connections"
}

purge_retained_data() {
  remove_tree "$CONNECT_STATE_DIR" "/var/lib/deployd-connect"
  remove_tree "$RELEASE_ROOT" "/srv/deployd"
}

purge_tooling() {
  local name
  for name in uv uvx pnpm pnpx; do
    rm -f -- "$TOOL_BIN_DIR/$name"
  done
  rm -rf -- "$HOME/.cache/uv" "$HOME/.cache/node/corepack"
}

purge_journal() {
  sudo journalctl --rotate && sudo journalctl --vacuum-time=1s
}

main() {
  parse_args "$@"
  require_uninstall_user
  check_checkout
  cd "$REPO_ROOT"
  show_scope

  local backup_path=""
  if choose_backup; then
    backup_path="$HOME/deployd-uninstall-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    backup_path=$(realpath -m "$backup_path")
    case "$backup_path" in
      "$REPO_ROOT"/* | "$STATE_DIR"/*) die "backup must be outside the directories being removed" ;;
    esac
  fi
  confirm_removal
  sudo -v
  check_removal_targets

  local preserve_identity="false"
  if [[ $purge != "true" ]] && apps_configured; then
    preserve_identity="true"
  fi
  if systemctl is-active --quiet deployd.service; then
    # Any failure after the stop restarts the service exactly once, via this trap.
    trap 'if [[ $? -ne 0 ]] && sudo test -f /etc/systemd/system/deployd.service; then sudo systemctl start deployd.service || true; fi' EXIT
  fi
  stop_service_safely
  if [[ -n $backup_path ]]; then
    (create_backup "$backup_path") || die "uninstall cancelled because the backup failed"
    printf 'Backup created: %s\n' "$backup_path"
  fi

  restore_websites
  remove_nginx_config
  remove_service
  remove_file "$HELPER_SUDOERS"
  remove_file "$HELPER_BIN"
  sudo rmdir "$(dirname -- "$HELPER_BIN")" 2>/dev/null || true
  remove_tree "$STATE_DIR" "/var/lib/deployd"
  sudo rmdir "$RELEASE_ROOT" 2>/dev/null || true
  if [[ $purge == "true" ]]; then
    purge_retained_data
  fi
  remove_service_identity

  cd /
  remove_tree "$REPO_ROOT" "/opt/deployd"
  if [[ $purge == "true" ]]; then
    purge_tooling
    purge_journal
  fi

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
