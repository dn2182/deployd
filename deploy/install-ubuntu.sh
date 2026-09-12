#!/usr/bin/env bash

set -Eeuo pipefail

readonly PNPM_VERSION="11.20.0"
readonly STATE_DIR="/var/lib/deployd"
readonly SERVICE_FILE="/etc/systemd/system/deployd.service"
readonly NGINX_SITE="/etc/nginx/sites-available/deployd"
readonly NGINX_LINK="/etc/nginx/sites-enabled/deployd"
readonly HTPASSWD_FILE="/etc/nginx/deployd.htpasswd"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_install_user() {
  [[ $(uname -s) == Linux ]] || die "this installer requires Debian/Ubuntu with systemd"
  [[ ${EUID} -ne 0 ]] || die "run this installer as the repository owner, without sudo"
  command -v sudo >/dev/null 2>&1 || die "sudo is required for system configuration"
}

prompt_default() {
  local prompt=$1
  local default=$2
  local value
  read -r -p "${prompt} [${default}]: " value
  printf '%s' "${value:-$default}"
}

confirm_testing_mode() {
  local answer
  printf '%s\n' \
    "Cloudflare Flexible mode leaves Cloudflare-to-origin traffic unencrypted." \
    "The management port must remain blocked by the firewall or restricted to a trusted network."
  read -r -p "Continue with this testing-only setup? [y/N]: " answer
  [[ ${answer,,} == "y" || ${answer,,} == "yes" ]] || exit 0
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

install_prerequisites() {
  install -d -m 0755 "$HOME/.local/bin"
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git make nginx apache2-utils openssl python3

  if ! command -v node >/dev/null 2>&1; then
    sudo apt-get install -y nodejs npm
  fi

  if ! command -v uv >/dev/null 2>&1; then
    local installer
    installer=$(mktemp /tmp/uv-installer.XXXXXX)
    curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
    env UV_INSTALL_DIR="$HOME/.local/bin" sh "$installer"
    unlink "$installer"
  fi

  command -v node >/dev/null 2>&1 || die "Node.js installation failed"
  local node_ok
  node_ok=$(node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.stdout.write(String(a>22||(a===22&&b>=19)))')
  [[ $node_ok == "true" ]] || die "Node.js 22.19 or newer is required"

  if ! command -v pnpm >/dev/null 2>&1; then
    if command -v corepack >/dev/null 2>&1; then
      corepack enable --install-directory "$HOME/.local/bin"
      corepack prepare "pnpm@${PNPM_VERSION}" --activate
    else
      command -v npm >/dev/null 2>&1 || sudo apt-get install -y npm
      npm install --global --prefix "$HOME/.local" "pnpm@${PNPM_VERSION}"
    fi
  fi
  command -v uv >/dev/null 2>&1 || die "uv installation failed"
  command -v pnpm >/dev/null 2>&1 || die "pnpm installation failed"
}

check_checkout() {
  local repo_root=$1
  [[ $repo_root == "/opt/deployd" ]] || die "clone deployd at /opt/deployd before running"
  [[ ! -L $repo_root ]] || die "the checkout must not be a symbolic link"
  [[ -f "$repo_root/pyproject.toml" && -f "$repo_root/web/package.json" ]] ||
    die "installer must run from the deployd repository"
  [[ $(stat -c '%u' "$repo_root") -eq ${EUID} ]] ||
    die "$repo_root must be owned by the user running the installer"
  [[ -z $(git -C "$repo_root" status --porcelain --untracked-files=no) ]] ||
    die "tracked repository changes detected; commit or restore them first"
}

repair_legacy_build_ownership() {
  local repo_root=$1
  local path
  for path in \
    "$repo_root/.python" \
    "$repo_root/.venv" \
    "$repo_root/.pytest_cache" \
    "$repo_root/.ruff_cache" \
    "$repo_root/web/node_modules" \
    "$repo_root/web/dist"; do
    [[ ! -L $path ]] || die "build directory must not be a symbolic link: $path"
    if [[ -d $path ]] && [[ -n $(find "$path" -xdev ! -uid "$(id -u)" -print -quit) ]]; then
      sudo chown -R "$(id -u):$(id -g)" "$path"
    fi
  done
}

install_application() {
  local repo_root=$1
  local effective_pnpm
  effective_pnpm=$(pnpm --dir "$repo_root/web" --version)
  [[ $effective_pnpm == "$PNPM_VERSION" ]] ||
    die "web/package.json requires pnpm ${PNPM_VERSION}, got ${effective_pnpm}"
  make -C "$repo_root" install
  make -C "$repo_root" lint
  make -C "$repo_root" test
  make -C "$repo_root" audit
  make -C "$repo_root" build
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
    sudo useradd --system --user-group --home-dir /opt/deployd --shell /usr/sbin/nologin deployd
  fi
  [[ $(id -u deployd) -ne 0 && $(id -gn deployd) == deployd ]] ||
    die "existing deployd identity must be non-root with primary group deployd"
  sudo install -d -o deployd -g deployd -m 0700 "$STATE_DIR"
  sudo test ! -L /srv/deployd || die "/srv/deployd must not be a symbolic link"
  sudo install -d -o deployd -g deployd -m 0755 /srv/deployd
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

  printf '%s' "$generated_token"
}

configure_basic_auth() {
  local username=$1
  sudo test ! -L "$HTPASSWD_FILE" || die "Basic Auth file must not be a symbolic link"
  if sudo test -f "$HTPASSWD_FILE"; then
    printf 'Keeping existing management Basic Auth file: %s\n' "$HTPASSWD_FILE"
  else
    printf 'Choose a separate password for the management web interface.\n'
    local password confirmation
    read -r -s -p "Management password: " password
    printf '\n'
    read -r -s -p "Confirm management password: " confirmation
    printf '\n'
    [[ -n $password && $password == "$confirmation" ]] || die "passwords are empty or do not match"
    printf '%s\n' "$password" | sudo htpasswd -iBc "$HTPASSWD_FILE" "$username"
  fi
  sudo chown root:www-data "$HTPASSWD_FILE"
  sudo chmod 0640 "$HTPASSWD_FILE"
}

render_nginx_config() {
  local output=$1
  local repo_root=$2
  local domain=$3
  local admin_bind=$4
  local admin_port=$5

  cat >"$output" <<EOF
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

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'" always;

    location /api/ {
        proxy_pass http://127.0.0.1:8300/;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        add_header Cache-Control "no-store" always;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
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
    backup=$(sudo mktemp "${NGINX_SITE}.backup.XXXXXXXX")
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

install_website_helper() {
  local repo_root=$1 answer path rule
  if [[ ! -f /etc/sudoers.d/deployd-connect ]]; then
    printf '%s\n' \
      'Optional: allow the management UI to connect existing static websites.' \
      'This grants deployd a restricted root helper for direct children of /var/www.' \
      'Each live switch requires UI confirmation and preserves the original as b4deployd.'
    read -r -p 'Enable website connection? [y/N]: ' answer
    [[ ${answer,,} == y || ${answer,,} == yes ]] || return 0
  fi
  for path in /usr/local /usr/local/libexec /usr/local/libexec/deployd /var/lib/deployd-connect; do
    sudo test ! -L "$path" || die "helper path must not be a symlink: $path"
    if sudo test -e "$path"; then
      [[ $(sudo stat -c '%U' "$path") == root ]] || die "helper path must be root-owned: $path"
      [[ $(sudo find "$path" -maxdepth 0 -perm /022 -print) == '' ]] || die "helper path must not be writable by other users: $path"
    fi
  done
  sudo install -d -o root -g root -m 0755 /usr/local/libexec /usr/local/libexec/deployd
  sudo install -d -o root -g root -m 0700 /var/lib/deployd-connect
  sudo test ! -L /usr/local/libexec/deployd/connect-website || die "helper executable must not be a symlink"
  sudo install -o root -g root -m 0755 "$repo_root/deploy/connect_website.py" /usr/local/libexec/deployd/connect-website
  rule=$(sudo mktemp /etc/sudoers.d/deployd-connect.XXXXXX)
  printf 'deployd ALL=(root) NOPASSWD: NOSETENV: /usr/local/libexec/deployd/connect-website\n' | sudo tee "$rule" >/dev/null
  sudo chmod 0440 "$rule"
  if ! sudo visudo -cf "$rule"; then
    sudo rm -f -- "$rule"
    die "website helper sudo policy did not validate"
  fi
  sudo mv -T -- "$rule" /etc/sudoers.d/deployd-connect
}

main() {
  require_install_user
  export PATH="$HOME/.local/bin:$PATH"
  confirm_testing_mode

  local script_dir repo_root domain admin_bind admin_port admin_username generated_token
  script_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  repo_root=$(cd -P -- "$script_dir/.." && pwd)
  domain=$(prompt_default "Public deployd domain" "deployd.example.com")
  admin_bind=$(prompt_default "Management bind address" "0.0.0.0")
  admin_port=$(prompt_default "Management port" "844")
  admin_username=$(prompt_default "Management Basic Auth username" "deployd-admin")

  validate_domain "$domain"
  validate_bind "$admin_bind"
  validate_port "$admin_port"
  admin_port=$((10#$admin_port))
  [[ $admin_username =~ ^[a-zA-Z0-9._-]{1,64}$ ]] || die "invalid Basic Auth username"

  check_checkout "$repo_root"
  install_prerequisites
  create_service_user
  stop_for_upgrade
  repair_legacy_build_ownership "$repo_root"
  install_application "$repo_root"
  generated_token=$(configure_runtime "$repo_root") || die "runtime setup failed"
  configure_basic_auth "$admin_username"
  install_website_helper "$repo_root"
  install_service "$repo_root"
  write_nginx_config "$repo_root" "$domain" "$admin_bind" "$admin_port"
  verify_installation "$domain" "$admin_port"

  printf '\nInstallation complete.\n'
  printf 'Public API: https://%s (Cloudflare proxy to origin port 80)\n' "$domain"
  printf 'Management UI: http://<server-ip>:%s\n' "$admin_port"
  printf 'Firewall management: not modified; keep port %s restricted.\n' "$admin_port"
  if [[ -n $generated_token ]]; then
    printf '\nDEPLOYD_ADMIN_TOKEN (shown once):\n%s\n' "$generated_token"
  else
    printf 'Existing admin token preserved; it was not displayed.\n'
  fi
  printf '\nSet Cloudflare SSL mode to Flexible only for this test setup.\n'
  printf 'Upgrade to Full (strict) before production use.\n'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
