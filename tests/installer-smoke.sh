#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux && $EUID -ne 0 ]] || {
  printf 'This lifecycle test is restricted to disposable GitHub Linux runners.\n' >&2
  exit 1
}
[[ ! -e /opt/deployd && ! -e /var/lib/deployd && ! -e /srv/deployd ]] || exit 1

checkout=$PWD
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0755 /opt/deployd
git clone --no-hardlinks "$checkout" /opt/deployd

printf 'y\ndeployd.example.com\n127.0.0.1\n844\nsmoke-admin\nsmoke-password\nsmoke-password\ny\n' |
  /opt/deployd/deploy/install-ubuntu.sh >"$RUNNER_TEMP/deployd-install.log" 2>&1
sudo systemctl is-active --quiet deployd
curl --fail --silent http://127.0.0.1:8300/healthz
poll=$(curl --silent --show-error --max-time 5 -H 'Host: deployd.example.com' \
  http://127.0.0.1/deploys/00000000000000000000000000000000)
[[ $poll == '{"detail":"unknown deploy"}' ]]
curl --fail --silent -u smoke-admin:smoke-password http://127.0.0.1:844/ >/dev/null
[[ $(sudo stat -c '%U:%G:%a' /opt/deployd/.env) == root:deployd:640 ]]
[[ $(sudo stat -c '%U:%G:%a' /var/lib/deployd/secrets.env) == deployd:deployd:600 ]]
[[ $(stat -c '%u' /opt/deployd/web/node_modules) == "$(id -u)" ]]
[[ $(sudo stat -c '%U:%G:%a' /usr/local/libexec/deployd/connect-website) == root:root:755 ]]
[[ $(sudo stat -c '%U:%G:%a' /etc/sudoers.d/deployd-connect) == root:root:440 ]]
sudo env GITHUB_ACTIONS=true python3 "$checkout/tests/website-smoke.py"

sudo -u deployd touch /srv/deployd/retained-app-data
owner=$(id -u deployd)
before=$(sudo sha256sum /opt/deployd/.env /var/lib/deployd/apps.yaml /var/lib/deployd/secrets.env)
printf 'y\ndeployd.example.com\n127.0.0.1\n844\nsmoke-admin\n' |
  /opt/deployd/deploy/install-ubuntu.sh >"$RUNNER_TEMP/deployd-upgrade.log" 2>&1
after=$(sudo sha256sum /opt/deployd/.env /var/lib/deployd/apps.yaml /var/lib/deployd/secrets.env)
[[ $before == "$after" ]]
sudo systemctl is-active --quiet deployd

printf 'y\nREMOVE deployd\n' | /opt/deployd/deploy/uninstall-ubuntu.sh
[[ ! -e /opt/deployd && ! -e /var/lib/deployd ]]
[[ ! -e /etc/nginx/sites-enabled/deployd && ! -e /etc/systemd/system/deployd.service ]]
[[ $(id -u deployd) == "$owner" && -f /srv/deployd/retained-app-data ]]
[[ ! -e /etc/sudoers.d/deployd-connect && ! -e /usr/local/libexec/deployd/connect-website ]]
[[ -L /var/www/deployd-smoke.example && -f /srv/deployd/website-smoke/releases/b4deployd/index.html ]]
sudo test -f /var/lib/deployd-connect/website-smoke/complete.json
sudo nginx -t
backup=$(find "$HOME" -maxdepth 1 -name 'deployd-uninstall-*.tar.gz' -print -quit)
[[ -n $backup && $(stat -c '%a' "$backup") == 600 ]]
tar -tzf "$backup" | sed -n '/var\/lib\/deployd\/secrets.env/p' | grep -q secrets.env
printf 'Fresh install, upgrade, backup, and uninstall passed.\n'
