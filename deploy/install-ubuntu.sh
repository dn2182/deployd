#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

readonly PNPM_VERSION="11.20.0"
readonly NODE_VERSION="22.23.2"
readonly NODE_MIN_MAJOR=22
readonly NODE_MIN_MINOR=19
readonly UV_VERSION="0.12.13"
readonly APT_PACKAGES=(ca-certificates curl git make nginx apache2-utils openssl python3)

require_install_user() {
  require_normal_linux_user installer
}

# Every prompt honours an environment override so the installer can run unattended.
prompt_default() {
  local variable=$1 prompt=$2 default=$3 value
  if [[ -n ${!variable:-} ]]; then
    printf '%s' "${!variable}"
    return 0
  fi
  read -r -p "${prompt} [${default}]: " value
  printf '%s' "${value:-$default}"
}

confirm_testing_mode() {
  printf '%s\n' \
    "Cloudflare Flexible mode leaves Cloudflare-to-origin traffic unencrypted." \
    "The management port must remain blocked by the firewall or restricted to a trusted network."
  confirm "Continue with this testing-only setup?" || die "installation declined"
}

validate_domain() {
  [[ $1 =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]] ||
    die "invalid domain name"
}

validate_bind() {
  [[ $1 == "127.0.0.1" || $1 == "0.0.0.0" ]] ||
    die "management bind must be 127.0.0.1 or 0.0.0.0"
}

validate_port() {
  [[ $1 =~ ^[0-9]{1,5}$ ]] || die "management port must contain 1-5 digits"
  local port=$((10#$1))
  ((1 <= port && port <= 65535)) || die "management port is outside 1-65535"
  [[ $port != 80 && $port != 445 && $port != 8300 ]] ||
    die "management port conflicts with HTTP, SMB, or deployd"
}

fetch() {
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --retry 3 \
    --output "$2" "$1"
}

apt_install_missing() {
  local package
  local -a missing=()
  for package in "${APT_PACKAGES[@]}"; do
    [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null) == installed ]] ||
      missing+=("$package")
  done
  if ((${#missing[@]})); then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
  fi
}

node_meets_minimum() {
  local binary=$1 version major minor
  [[ -n $binary && -x $binary ]] || return 1
  version=$("$binary" --version 2>/dev/null) || return 1
  IFS=. read -r major minor _ <<<"${version#v}"
  [[ $major =~ ^[0-9]+$ && $minor =~ ^[0-9]+$ ]] || return 1
  ((major > NODE_MIN_MAJOR || (major == NODE_MIN_MAJOR && minor >= NODE_MIN_MINOR)))
}

node_arch() {
  case "$(uname -m)" in
    x86_64) printf 'x64' ;;
    aarch64) printf 'arm64' ;;
    *) die "unsupported architecture for Node.js: $(uname -m)" ;;
  esac
}

install_node() {
  local arch tarball workdir expected base
  arch=$(node_arch)
  tarball="node-v${NODE_VERSION}-linux-${arch}.tar.gz"
  base="https://nodejs.org/dist/v${NODE_VERSION}"
  workdir=$(mktemp -d /tmp/deployd-node.XXXXXX)
  fetch "$base/$tarball" "$workdir/$tarball"
  fetch "$base/SHASUMS256.txt" "$workdir/SHASUMS256.txt"
  expected=$(awk -v name="$tarball" '$2 == name { print $1 }' "$workdir/SHASUMS256.txt")
  [[ $expected =~ ^[0-9a-f]{64}$ ]] || die "SHASUMS256.txt has no entry for $tarball"
  (cd "$workdir" && printf '%s  %s\n' "$expected" "$tarball" | sha256sum -c --quiet --strict -) ||
    die "Node.js checksum verification failed"
  [[ ! -L $NODE_DIR ]] || die "$NODE_DIR must not be a symbolic link"
  rm -rf -- "$NODE_DIR"
  install -d -m 0755 "$NODE_DIR"
  tar -xzf "$workdir/$tarball" -C "$NODE_DIR" --strip-components=1
  rm -rf -- "$workdir"
}

ensure_node() {
  if node_meets_minimum "$NODE_DIR/bin/node" &&
    [[ $("$NODE_DIR/bin/node" --version) == "v${NODE_VERSION}" ]]; then
    return 0
  fi
  if node_meets_minimum "$(command -v node || true)"; then
    return 0
  fi
  install_node
  node_meets_minimum "$NODE_DIR/bin/node" || die "Node.js installation failed"
}

uv_target() {
  case "$(uname -m)" in
    x86_64) printf 'x86_64-unknown-linux-gnu' ;;
    aarch64) printf 'aarch64-unknown-linux-gnu' ;;
    *) die "unsupported architecture for uv: $(uname -m)" ;;
  esac
}

install_uv() {
  local target tarball base workdir
  target=$(uv_target)
  tarball="uv-${target}.tar.gz"
  base="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
  workdir=$(mktemp -d /tmp/deployd-uv.XXXXXX)
  fetch "$base/$tarball" "$workdir/$tarball"
  fetch "$base/$tarball.sha256" "$workdir/$tarball.sha256"
  (cd "$workdir" && sha256sum -c --quiet --strict "$tarball.sha256") ||
    die "uv checksum verification failed"
  tar -xzf "$workdir/$tarball" -C "$workdir"
  install -d -m 0755 "$TOOL_BIN_DIR"
  install -m 0755 "$workdir/uv-${target}/uv" "$workdir/uv-${target}/uvx" "$TOOL_BIN_DIR/"
  rm -rf -- "$workdir"
}

ensure_uv() {
  command -v uv >/dev/null 2>&1 && return 0
  install_uv
  command -v uv >/dev/null 2>&1 || die "uv installation failed"
}

ensure_pnpm() {
  if ! command -v pnpm >/dev/null 2>&1; then
    command -v corepack >/dev/null 2>&1 || die "corepack is missing from the Node.js installation"
    corepack enable --install-directory "$TOOL_BIN_DIR"
    corepack prepare "pnpm@${PNPM_VERSION}" --activate
  fi
  command -v pnpm >/dev/null 2>&1 || die "pnpm installation failed"
}

install_prerequisites() {
  install -d -m 0755 "$TOOL_BIN_DIR"
  apt_install_missing
  ensure_node
  ensure_uv
  ensure_pnpm
}

check_checkout() {
  local repo_root=$1
  [[ $repo_root == "$REPO_ROOT" ]] || die "clone deployd at $REPO_ROOT before running"
  [[ ! -L $repo_root ]] || die "the checkout must not be a symbolic link"
  [[ -f "$repo_root/pyproject.toml" && -f "$repo_root/web/package.json" ]] ||
    die "installer must run from the deployd repository"
  [[ $(stat -c '%u' "$repo_root") -eq ${EUID} ]] ||
    die "$repo_root must be owned by the user running the installer"
  [[ -z $(git -C "$repo_root" status --porcelain --untracked-files=no) ]] ||
    die "tracked repository changes detected; commit or restore them first"
}

# Listeners on 80 and the management port must be nginx; 8300 must be free unless deployd holds it.
check_ports() {
  local admin_port=$1 port listener
  for port in 80 "$admin_port"; do
    listener=$(sudo ss -Hltnp "sport = :${port}")
    [[ -z $listener || $listener == *'"nginx"'* ]] ||
      die "port ${port} is used by another service: ${listener}"
  done
  if ! systemctl is-active --quiet deployd.service; then
    listener=$(sudo ss -Hltnp 'sport = :8300')
    [[ -z $listener ]] || die "port 8300 is used by another service: ${listener}"
  fi
}

repair_legacy_build_ownership() {
  local repo_root=$1
  local path
  for path in \
    "$repo_root/.python" \
    "$repo_root/.node" \
    "$repo_root/.venv" \
    "$repo_root/.pytest_cache" \
    "$repo_root/.ruff_cache" \
    "$repo_root/web/node_modules" \
    "$repo_root/web/dist"; do
    [[ ! -L $path ]] || die "build directory must not be a symbolic link: $path"
    if [[ -d $path ]] && [[ -n $(sudo find "$path" -xdev ! -uid "$(id -u)" -print -quit) ]]; then
      sudo chown -R "$(id -u):$(id -g)" "$path"
    fi
  done
}

# Validation and the web build run while the current service keeps serving.
prepare_application() {
  local repo_root=$1
  local effective_pnpm
  effective_pnpm=$(pnpm --dir "$repo_root/web" --version)
  [[ $effective_pnpm == "$PNPM_VERSION" ]] ||
    die "web/package.json requires pnpm ${PNPM_VERSION}, got ${effective_pnpm}"
  make -C "$repo_root" install-web
  if [[ ${DEPLOYD_INSTALL_SKIP_CHECKS:-} == 1 && ${GITHUB_ACTIONS:-} == true ]]; then
    printf 'Skipping lint, tests and audit; CI runs them in dedicated jobs.\n'
  else
    make -C "$repo_root" check
  fi
  make -C "$repo_root" build
}

install_application() {
  local repo_root=$1
  make -C "$repo_root" install
  local interpreter
  interpreter=$(readlink -f "$repo_root/.venv/bin/python")
  [[ $interpreter == /usr/* || $interpreter == /opt/* ]] ||
    die "Python resolves to $interpreter; recreate .venv with Python under /usr or /opt (ProtectHome blocks home directories)"
  chmod 0755 "$repo_root" "$repo_root/web" "$repo_root/web/dist"
  chmod -R a+rX "$repo_root/.venv"
  if [[ -d "$repo_root/.python" ]]; then
    chmod -R a+rX "$repo_root/.python"
  fi
  find "$repo_root/src" -type d -exec chmod 0755 {} +
  find "$repo_root/src" -type f -exec chmod 0644 {} +
  find "$repo_root/web/dist" -type d -exec chmod 0755 {} +
  find "$repo_root/web/dist" -type f -exec chmod 0644 {} +
}

create_service_user() {
  sudo test ! -L "$STATE_DIR" || die "$STATE_DIR must not be a symbolic link"
  if ! id deployd >/dev/null 2>&1; then
    sudo useradd --system --user-group --home-dir "$REPO_ROOT" --shell /usr/sbin/nologin deployd
  fi
  [[ $(id -u deployd) -ne 0 && $(id -gn deployd) == deployd ]] ||
    die "existing deployd identity must be non-root with primary group deployd"
  sudo install -d -o deployd -g deployd -m 0700 "$STATE_DIR"
  sudo test ! -L "$RELEASE_ROOT" || die "$RELEASE_ROOT must not be a symbolic link"
  sudo install -d -o deployd -g deployd -m 0755 "$RELEASE_ROOT"
}

runtime_db_path() {
  local repo_root=$1 value=""
  if sudo test -f "$repo_root/.env"; then
    value=$(sudo python3 -I - "$repo_root/.env" <<'PY'
import sys

value = ""
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        key, separator, raw = line.strip().partition("=")
        if separator and key.strip() == "DEPLOYD_DB_PATH":
            value = raw.strip().strip("'\"")
print(value)
PY
    ) || die "could not read DEPLOYD_DB_PATH from $repo_root/.env"
  fi
  printf '%s' "${value:-$STATE_DIR/deployd.sqlite3}"
}

pending_deploys() {
  sudo -u deployd env -i PATH=/usr/bin:/bin python3 -I - "$1" <<'PY'
import sqlite3
import sys
from pathlib import Path

conn = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
try:
    row = conn.execute(
        "SELECT count(*) FROM deploys WHERE status IN ('queued', 'running')"
    ).fetchone()
    print(row[0])
except sqlite3.OperationalError:
    print(0)
finally:
    conn.close()
PY
}

wait_for_idle_queue() {
  local db=$1 timeout=${DEPLOYD_INSTALL_WAIT_SECONDS:-600} waited=0 count
  systemctl is-active --quiet deployd.service || return 0
  sudo test -f "$db" || return 0
  count=$(pending_deploys "$db") || die "could not read the deploy queue"
  [[ $count =~ ^[0-9]+$ ]] || die "unexpected deploy queue state: $count"
  ((count > 0)) || return 0
  printf '%s deploy(s) queued or running; deployd is not stopped while deploys are active.\n' "$count"
  confirm "Wait up to ${timeout}s for them to finish?" ||
    die "upgrade cancelled; the running service was not changed"
  while ((waited < timeout)); do
    sleep 5
    waited=$((waited + 5))
    count=$(pending_deploys "$db") || die "could not read the deploy queue"
    [[ $count =~ ^[0-9]+$ ]] || die "unexpected deploy queue state: $count"
    ((count > 0)) || return 0
  done
  die "deploys still active after ${timeout}s; rerun the installer later"
}

backup_state_db() {
  local db=$1 target
  sudo test -f "$db" || return 0
  sudo test ! -L "$BACKUP_DIR" || die "$BACKUP_DIR must not be a symbolic link"
  sudo install -d -o deployd -g deployd -m 0700 "$BACKUP_DIR"
  target="$BACKUP_DIR/deployd-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  sudo -u deployd env -i PATH=/usr/bin:/bin python3 -I - "$db" "$target" <<'PY' || die "state backup failed"
import sqlite3
import sys
from pathlib import Path

source = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
target = sqlite3.connect(sys.argv[2])
try:
    with target:
        source.backup(target)
finally:
    target.close()
    source.close()
PY
  sudo chmod 0600 "$target"
  prune_privileged_files "$BACKUP_DIR" 5 'deployd-*.sqlite3'
  printf 'State backed up to %s\n' "$target"
}

configure_runtime() {
  local repo_root=$1
  local generated_token
  generated_token=$(sudo "$repo_root/.venv/bin/python" "$repo_root/deploy/runtime_config.py" \
    prepare --repo "$repo_root" --state "$STATE_DIR") || die "runtime configuration failed"
  sudo chown root:deployd "$repo_root/.env" || die "could not set .env ownership"
  sudo chmod 0640 "$repo_root/.env" || die "could not set .env permissions"

  if sudo test -L "$STATE_DIR/apps.yaml" || sudo test -L "$STATE_DIR/secrets.env"; then
    die "runtime configuration files must not be symbolic links"
  fi
  if ! sudo test -f "$STATE_DIR/apps.yaml"; then
    local apps_file
    apps_file=$(mktemp /tmp/deployd-apps.XXXXXX) || die "could not create temporary app config"
    printf 'apps: {}\n' >"$apps_file" || die "could not write temporary app config"
    sudo install -o deployd -g deployd -m 0600 "$apps_file" "$STATE_DIR/apps.yaml" ||
      die "could not install apps.yaml"
    unlink "$apps_file" || die "could not remove temporary app config"
  fi
  if ! sudo test -f "$STATE_DIR/secrets.env"; then
    sudo install -o deployd -g deployd -m 0600 /dev/null "$STATE_DIR/secrets.env" ||
      die "could not install secrets.env"
  fi

  local path
  for path in "$STATE_DIR/apps.yaml" "$STATE_DIR/secrets.env" \
    "$STATE_DIR/deployd.sqlite3" "$STATE_DIR/deployd.sqlite3.lock" \
    "$STATE_DIR/deployd.sqlite3-wal" "$STATE_DIR/deployd.sqlite3-shm"; do
    sudo test ! -L "$path" || die "runtime file must not be a symbolic link: $path"
    if sudo test -e "$path"; then
      sudo test -f "$path" || die "runtime path is not a regular file: $path"
      sudo chown deployd:deployd "$path" || die "could not repair ownership: $path"
      sudo chmod 0600 "$path" || die "could not protect runtime file: $path"
    fi
  done

  sudo -u deployd env -i PATH=/usr/bin:/bin \
    "$repo_root/.venv/bin/python" "$repo_root/deploy/runtime_config.py" \
    check --repo "$repo_root" || die "runtime preflight failed; service was not restarted"

  [[ -n $generated_token ]] && printf 'generated'
  return 0
}

existing_basic_auth_user() {
  sudo test -f "$HTPASSWD_FILE" || return 1
  sudo cut -d: -f1 "$HTPASSWD_FILE" | head -n 1
}

configure_basic_auth() {
  local username=$1
  sudo test ! -L "$HTPASSWD_FILE" || die "Basic Auth file must not be a symbolic link"
  if sudo test -f "$HTPASSWD_FILE"; then
    printf 'Keeping existing management Basic Auth file: %s\n' "$HTPASSWD_FILE"
  else
    local password confirmation
    if [[ -n ${DEPLOYD_INSTALL_ADMIN_PASSWORD:-} ]]; then
      password=$DEPLOYD_INSTALL_ADMIN_PASSWORD
      confirmation=$password
    else
      printf 'Choose a separate password for the management web interface.\n'
      read -r -s -p "Management password: " password
      printf '\n'
      read -r -s -p "Confirm management password: " confirmation
      printf '\n'
    fi
    [[ -n $password && $password == "$confirmation" ]] || die "passwords are empty or do not match"
    printf '%s\n' "$password" | sudo htpasswd -iBc "$HTPASSWD_FILE" "$username"
  fi
  sudo chown root:www-data "$HTPASSWD_FILE"
  sudo chmod 0640 "$HTPASSWD_FILE"
}

render_security_headers() {
  cat <<'EOF'
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
        add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'" always;
EOF
}

render_nginx_config() {
  local output=$1
  local repo_root=$2
  local domain=$3
  local admin_bind=$4
  local admin_port=$5
  local headers
  headers=$(render_security_headers)

  # add_header does not inherit into locations that set their own, so the set is repeated per location.
  cat >"$output" <<EOF
limit_req_zone \$binary_remote_addr zone=deployd_deploys:1m rate=10r/m;

server {
    listen 80;
    server_name ${domain};
    server_tokens off;
    client_max_body_size 64k;

    location = /healthz {
        proxy_pass http://127.0.0.1:8300;
        proxy_set_header Host \$host;
        add_header Cache-Control "no-store" always;
    }

    location = /deploys {
        limit_except POST { deny all; }
        limit_req zone=deployd_deploys burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8300;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        add_header Cache-Control "no-store" always;
    }

    location ~* "^/deploys/[0-9a-f]{32}\$" {
        limit_except GET { deny all; }
        proxy_pass http://127.0.0.1:8300;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        add_header Cache-Control "no-store" always;
    }

    location / {
        return 404;
    }
}

server {
    listen ${admin_bind}:${admin_port};
    server_name _;
    server_tokens off;
    root ${repo_root}/web/dist;
    index index.html;
    client_max_body_size 64k;

    auth_basic "deployd management";
    auth_basic_user_file ${HTPASSWD_FILE};

    location /api/ {
        proxy_pass http://127.0.0.1:8300/;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Remote-User \$remote_user;
${headers}
        add_header Cache-Control "no-store" always;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
${headers}
    }
}
EOF
}

activate_nginx() {
  if systemctl is-active --quiet nginx; then
    sudo systemctl reload nginx
  else
    sudo systemctl start nginx
  fi
}

write_nginx_config() {
  local repo_root=$1
  local domain=$2
  local admin_bind=$3
  local admin_port=$4
  local candidate backup="" link_created="false"
  candidate=$(mktemp /tmp/deployd-nginx.XXXXXX)
  render_nginx_config "$candidate" "$repo_root" "$domain" "$admin_bind" "$admin_port"
  if sudo test -L "$NGINX_SITE"; then
    unlink "$candidate"
    die "$NGINX_SITE must not be a symbolic link"
  fi

  if sudo test -e "$NGINX_LINK" && ! sudo test -L "$NGINX_LINK"; then
    unlink "$candidate"
    die "$NGINX_LINK exists and is not a symbolic link"
  fi
  if sudo test -L "$NGINX_LINK" && [[ $(readlink -f "$NGINX_LINK") != "$NGINX_SITE" ]]; then
    unlink "$candidate"
    die "$NGINX_LINK points to a different site"
  fi
  if sudo test -f "$NGINX_SITE"; then
    backup="${NGINX_SITE}.backup.$(date -u +%Y%m%dT%H%M%SZ)"
    sudo cp -a "$NGINX_SITE" "$backup"
  fi

  sudo install -o root -g root -m 0644 "$candidate" "$NGINX_SITE"
  unlink "$candidate"
  if ! sudo test -L "$NGINX_LINK"; then
    sudo ln -s "$NGINX_SITE" "$NGINX_LINK"
    link_created="true"
  fi

  if ! sudo nginx -t; then
    if [[ -n $backup ]]; then
      sudo cp -a "$backup" "$NGINX_SITE"
    else
      sudo unlink "$NGINX_SITE"
    fi
    if [[ $link_created == "true" ]]; then
      sudo unlink "$NGINX_LINK"
    fi
    sudo nginx -t || true
    die "Nginx rejected the generated configuration; the previous configuration was restored"
  fi
  if ! activate_nginx; then
    if [[ -n $backup ]]; then
      sudo cp -a "$backup" "$NGINX_SITE"
    else
      sudo unlink "$NGINX_SITE"
    fi
    if [[ $link_created == "true" ]]; then
      sudo unlink "$NGINX_LINK"
    fi
    sudo systemctl reload nginx || true
    die "Nginx activation failed; the previous configuration was restored"
  fi
  sudo systemctl enable nginx
  prune_privileged_files "$(dirname -- "$NGINX_SITE")" 3 'deployd.backup.*'
}

install_service() {
  local repo_root=$1
  sudo test ! -L "$SERVICE_FILE" || die "$SERVICE_FILE must not be a symbolic link"
  sudo install -o root -g root -m 0644 "$repo_root/deploy/deployd.service" "$SERVICE_FILE"
  sudo systemctl daemon-reload
  sudo systemctl enable deployd
  sudo systemctl restart deployd
}

verify_installation() {
  local domain=$1
  local admin_port=$2
  local attempt ready="false"
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if curl --fail --silent --connect-timeout 1 --max-time 2 \
      http://127.0.0.1:8300/healthz >/dev/null; then
      ready="true"
      break
    fi
    if systemctl is-failed --quiet deployd; then
      break
    fi
    sleep 1
  done
  [[ $ready == "true" ]] ||
    die "deployd failed its health check; run: sudo journalctl -u deployd -n 60 --no-pager"
  curl --fail --silent --show-error --max-time 5 \
    -H "Host: ${domain}" http://127.0.0.1/healthz >/dev/null

  local admin_status
  admin_status=$(curl --silent --connect-timeout 2 --max-time 5 --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${admin_port}/")
  [[ $admin_status == "401" ]] || die "management endpoint did not require Basic Auth"
}

stop_for_upgrade() {
  if systemctl is-active --quiet deployd.service; then
    printf 'Stopping deployd while its dependencies and runtime configuration are updated.\n'
    sudo systemctl stop deployd.service
  fi
  if systemctl is-active --quiet deployd.service; then
    die "deployd is still running; no application files were changed"
  fi
}

check_website_parent() {
  sudo test ! -L /var/www || die '/var/www must be a real directory, not a symlink'
  if sudo test -e /var/www; then
    sudo test -d /var/www || die '/var/www must be a directory'
    [[ $(sudo stat -c '%U' /var/www) == root ]] || die '/var/www must be root-owned; ask the server administrator to review ownership'
    [[ $(sudo find /var/www -maxdepth 0 -perm /022 -print) == '' ]] || die \
      '/var/www is group- or world-writable. Review and run: sudo chmod go-w /var/www. This restricts creating/renaming its direct children; it does not change site contents. Then rerun the installer. No permissions were changed automatically.'
  fi
}

install_website_helper() {
  local repo_root=$1 path rule
  if ! sudo test -f "$HELPER_SUDOERS"; then
    printf '%s\n' \
      'Optional: allow the management UI to connect existing static websites.' \
      'This grants deployd a restricted root helper for direct children of /var/www.' \
      'Each live switch requires UI confirmation and preserves the original as b4deployd.'
    confirm 'Enable website connection?' || return 0
  fi
  check_website_parent
  for path in /usr/local /usr/local/libexec /usr/local/libexec/deployd "$CONNECT_STATE_DIR"; do
    sudo test ! -L "$path" || die "helper path must not be a symlink: $path"
    if sudo test -e "$path"; then
      [[ $(sudo stat -c '%U' "$path") == root ]] || die "helper path must be root-owned: $path"
      [[ $(sudo find "$path" -maxdepth 0 -perm /022 -print) == '' ]] || die "helper path must not be writable by other users: $path"
    fi
  done
  if ! sudo test -e /var/www; then
    sudo install -d -o root -g root -m 0755 /var/www
  fi
  sudo install -d -o root -g root -m 0755 /usr/local/libexec /usr/local/libexec/deployd
  sudo install -d -o root -g root -m 0700 "$CONNECT_STATE_DIR"
  sudo test ! -L "$HELPER_BIN" || die "helper executable must not be a symlink"
  sudo install -o root -g root -m 0755 "$repo_root/deploy/connect_website.py" "$HELPER_BIN"
  rule=$(sudo mktemp "${HELPER_SUDOERS}.XXXXXX")
  printf 'deployd ALL=(root) NOPASSWD: NOSETENV: %s\n' "$HELPER_BIN" | sudo tee "$rule" >/dev/null
  sudo chmod 0440 "$rule"
  if ! sudo visudo -cf "$rule"; then
    sudo rm -f -- "$rule"
    die "website helper sudo policy did not validate"
  fi
  sudo mv -T -- "$rule" "$HELPER_SUDOERS"
}

main() {
  require_install_user
  export PATH="$NODE_DIR/bin:$TOOL_BIN_DIR:$PATH"
  confirm_testing_mode

  local script_dir repo_root domain admin_bind admin_port admin_username token_state db_path
  script_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  repo_root=$(cd -P -- "$script_dir/.." && pwd)
  domain=$(prompt_default DEPLOYD_INSTALL_DOMAIN "Public deployd domain" "deployd.example.com")
  admin_bind=$(prompt_default DEPLOYD_INSTALL_ADMIN_BIND "Management bind address" "127.0.0.1")
  admin_port=$(prompt_default DEPLOYD_INSTALL_ADMIN_PORT "Management port" "844")
  if admin_username=$(existing_basic_auth_user); then
    printf 'Management Basic Auth user (existing): %s\n' "$admin_username"
  else
    admin_username=$(prompt_default DEPLOYD_INSTALL_ADMIN_USER "Management Basic Auth username" "deployd-admin")
  fi

  validate_domain "$domain"
  validate_bind "$admin_bind"
  validate_port "$admin_port"
  admin_port=$((10#$admin_port))
  [[ $admin_username =~ ^[a-zA-Z0-9._-]{1,64}$ ]] || die "invalid Basic Auth username"

  check_checkout "$repo_root"
  install_prerequisites
  create_service_user
  if sudo test -f "$HELPER_SUDOERS"; then
    check_website_parent
  fi
  check_ports "$admin_port"
  repair_legacy_build_ownership "$repo_root"
  prepare_application "$repo_root"
  db_path=$(runtime_db_path "$repo_root")
  wait_for_idle_queue "$db_path"
  backup_state_db "$db_path"
  stop_for_upgrade
  install_application "$repo_root"
  token_state=$(configure_runtime "$repo_root") || die "runtime setup failed"
  configure_basic_auth "$admin_username"
  install_website_helper "$repo_root"
  install_service "$repo_root"
  write_nginx_config "$repo_root" "$domain" "$admin_bind" "$admin_port"
  verify_installation "$domain" "$admin_port"

  printf '\nInstallation complete.\n'
  printf 'Public API: https://%s (Cloudflare proxy to origin port 80)\n' "$domain"
  printf 'Management UI: http://%s:%s\n' "$admin_bind" "$admin_port"
  printf 'Firewall management: not modified; keep port %s restricted.\n' "$admin_port"
  if [[ $token_state == generated ]]; then
    printf '\nAdmin token generated in %s/.env; read it with:\n  sudo grep DEPLOYD_ADMIN_TOKEN %s/.env\n' "$repo_root" "$repo_root"
  else
    printf 'Existing admin token preserved in %s/.env.\n' "$repo_root"
  fi
  printf '\nSet Cloudflare SSL mode to Flexible only for this test setup.\n'
  printf 'Upgrade to Full (strict) before production use.\n'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
