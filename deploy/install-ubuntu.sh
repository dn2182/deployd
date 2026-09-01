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

require_root() {
  [[ ${EUID} -eq 0 ]] || die "run this installer with sudo"
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
  [[ $1 =~ ^[0-9]+$ ]] || die "management port must be numeric"
  ((1 <= $1 && $1 <= 65535)) || die "management port is outside 1-65535"
  [[ $1 != 80 && $1 != 445 && $1 != 8300 ]] ||
    die "management port conflicts with HTTP, SMB, or deployd"
}

install_prerequisites() {
  apt-get update
  apt-get install -y ca-certificates curl git make nginx apache2-utils openssl

  if ! command -v node >/dev/null 2>&1; then
    apt-get install -y nodejs npm
  fi

  if ! command -v uv >/dev/null 2>&1; then
    local installer
    installer=$(mktemp /tmp/uv-installer.XXXXXX)
    curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
    env UV_INSTALL_DIR=/usr/local/bin sh "$installer"
    unlink "$installer"
  fi

  command -v node >/dev/null 2>&1 || die "Node.js installation failed"
  local node_ok
  node_ok=$(node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.stdout.write(String(a>22||(a===22&&b>=19)))')
  [[ $node_ok == "true" ]] || die "Node.js 22.19 or newer is required"

  if ! command -v pnpm >/dev/null 2>&1; then
    if command -v corepack >/dev/null 2>&1; then
      corepack enable
      corepack prepare "pnpm@${PNPM_VERSION}" --activate
    else
      command -v npm >/dev/null 2>&1 || apt-get install -y npm
      npm install --global "pnpm@${PNPM_VERSION}"
    fi
  fi
}

check_checkout() {
  local repo_root=$1
  [[ $repo_root == "/opt/deployd" ]] || die "clone deployd at /opt/deployd before running"
  [[ -f "$repo_root/pyproject.toml" && -f "$repo_root/web/package.json" ]] ||
    die "installer must run from the deployd repository"
  [[ -z $(git -C "$repo_root" status --porcelain --untracked-files=no) ]] ||
    die "tracked repository changes detected; commit or restore them first"
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
  pnpm --dir "$repo_root/web" build
  chmod 0755 "$repo_root" "$repo_root/web" "$repo_root/web/dist"
  chmod -R a+rX "$repo_root/.venv"
  find "$repo_root/src" -type d -exec chmod 0755 {} +
  find "$repo_root/src" -type f -exec chmod 0644 {} +
  find "$repo_root/web/dist" -type d -exec chmod 0755 {} +
  find "$repo_root/web/dist" -type f -exec chmod 0644 {} +
}

create_service_user() {
  if ! id deployd >/dev/null 2>&1; then
    useradd --system --home-dir /opt/deployd --shell /usr/sbin/nologin deployd
  fi
  install -d -o deployd -g deployd -m 0700 "$STATE_DIR"
}

configure_runtime() {
  local repo_root=$1
  local generated_token=""

  if [[ ! -f "$repo_root/.env" ]]; then
    generated_token=$(openssl rand -hex 32)
    local env_file
    env_file=$(mktemp /tmp/deployd-env.XXXXXX)
    {
      printf 'DEPLOYD_DB_PATH=%s/deployd.sqlite3\n' "$STATE_DIR"
      printf 'DEPLOYD_APPS_CONFIG=%s/apps.yaml\n' "$STATE_DIR"
      printf 'DEPLOYD_SECRETS_FILE=%s/secrets.env\n' "$STATE_DIR"
      printf 'DEPLOYD_BIND_HOST=127.0.0.1\n'
      printf 'DEPLOYD_BIND_PORT=8300\n'
      printf 'DEPLOYD_MAX_REQUEST_BYTES=65536\n'
      printf 'DEPLOYD_ADMIN_TOKEN=%s\n' "$generated_token"
    } >"$env_file"
    install -o root -g root -m 0600 "$env_file" "$repo_root/.env"
    unlink "$env_file"
  fi

  if [[ ! -f "$STATE_DIR/apps.yaml" ]]; then
    local apps_file
    apps_file=$(mktemp /tmp/deployd-apps.XXXXXX)
    printf 'apps: {}\n' >"$apps_file"
    install -o deployd -g deployd -m 0600 "$apps_file" "$STATE_DIR/apps.yaml"
    unlink "$apps_file"
  fi
  if [[ ! -f "$STATE_DIR/secrets.env" ]]; then
    install -o deployd -g deployd -m 0600 /dev/null "$STATE_DIR/secrets.env"
  fi

  printf '%s' "$generated_token"
}

configure_basic_auth() {
  local username=$1
  if [[ -f $HTPASSWD_FILE ]]; then
    printf 'Keeping existing management Basic Auth file: %s\n' "$HTPASSWD_FILE"
  else
    printf 'Choose a separate password for the management web interface.\n'
    htpasswd -cB "$HTPASSWD_FILE" "$username"
    chown root:www-data "$HTPASSWD_FILE"
    chmod 0640 "$HTPASSWD_FILE"
  fi
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

    location ~* "^/deploys/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\$" {
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

write_nginx_config() {
  local repo_root=$1
  local domain=$2
  local admin_bind=$3
  local admin_port=$4
  local candidate backup="" link_created="false"
  candidate=$(mktemp /tmp/deployd-nginx.XXXXXX)
  render_nginx_config "$candidate" "$repo_root" "$domain" "$admin_bind" "$admin_port"

  if [[ -e $NGINX_LINK && ! -L $NGINX_LINK ]]; then
    unlink "$candidate"
    die "$NGINX_LINK exists and is not a symbolic link"
  fi
  if [[ -L $NGINX_LINK && $(readlink -f "$NGINX_LINK") != "$NGINX_SITE" ]]; then
    unlink "$candidate"
    die "$NGINX_LINK points to a different site"
  fi
  if [[ -f $NGINX_SITE ]]; then
    backup="${NGINX_SITE}.backup.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -a "$NGINX_SITE" "$backup"
  fi

  install -o root -g root -m 0644 "$candidate" "$NGINX_SITE"
  unlink "$candidate"
  if [[ ! -L $NGINX_LINK ]]; then
    ln -s "$NGINX_SITE" "$NGINX_LINK"
    link_created="true"
  fi

  if ! nginx -t; then
    if [[ -n $backup ]]; then
      cp -a "$backup" "$NGINX_SITE"
    else
      unlink "$NGINX_SITE"
    fi
    if [[ $link_created == "true" ]]; then
      unlink "$NGINX_LINK"
    fi
    nginx -t || true
    die "Nginx rejected the generated configuration; the previous configuration was restored"
  fi
  if ! systemctl reload nginx; then
    if [[ -n $backup ]]; then
      cp -a "$backup" "$NGINX_SITE"
    else
      unlink "$NGINX_SITE"
    fi
    if [[ $link_created == "true" ]]; then
      unlink "$NGINX_LINK"
    fi
    systemctl reload nginx || true
    die "Nginx reload failed; the previous configuration was restored"
  fi
}

install_service() {
  local repo_root=$1
  install -o root -g root -m 0644 "$repo_root/deploy/deployd.service" "$SERVICE_FILE"
  systemctl daemon-reload
  systemctl enable --now deployd
  systemctl restart deployd
}

verify_installation() {
  local domain=$1
  local admin_port=$2
  systemctl is-active --quiet deployd || die "deployd service is not active"
  local attempt
  for ((attempt = 1; attempt <= 20; attempt++)); do
    if curl --fail --silent http://127.0.0.1:8300/healthz >/dev/null; then
      break
    fi
    sleep 0.25
  done
  curl --fail --silent --show-error http://127.0.0.1:8300/healthz >/dev/null
  curl --fail --silent --show-error -H "Host: ${domain}" http://127.0.0.1/healthz >/dev/null

  local admin_status
  admin_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${admin_port}/")
  [[ $admin_status == "401" ]] || die "management endpoint did not require Basic Auth"
}

main() {
  require_root
  confirm_testing_mode

  local script_dir repo_root domain admin_bind admin_port admin_username generated_token
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  repo_root=$(cd -- "$script_dir/.." && pwd)
  domain=$(prompt_default "Public deployd domain" "deployd.example.com")
  admin_bind=$(prompt_default "Management bind address" "0.0.0.0")
  admin_port=$(prompt_default "Management port" "844")
  admin_username=$(prompt_default "Management Basic Auth username" "deployd-admin")

  validate_domain "$domain"
  validate_bind "$admin_bind"
  validate_port "$admin_port"
  [[ $admin_username =~ ^[a-zA-Z0-9._-]{1,64}$ ]] || die "invalid Basic Auth username"

  check_checkout "$repo_root"
  install_prerequisites
  create_service_user
  install_application "$repo_root"
  generated_token=$(configure_runtime "$repo_root")
  configure_basic_auth "$admin_username"
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
    printf 'Existing /opt/deployd/.env retained; its admin token was not displayed.\n'
  fi
  printf '\nSet Cloudflare SSL mode to Flexible only for this test setup.\n'
  printf 'Upgrade to Full (strict) before production use.\n'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
