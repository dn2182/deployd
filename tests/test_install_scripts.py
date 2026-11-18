import re
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


@pytest.mark.parametrize("writable", [True, False])
def test_website_preflight_reports_permissions_without_mutating_them(writable):
    result = run_function(
        "install-ubuntu.sh",
        """
      sudo() {
        case "$1" in
          test) return 0 ;;
          stat) echo root ;;
          find) """
        + ("echo /var/www" if writable else ":")
        + """ ;;
          *) echo unexpected-mutation; return 1 ;;
        esac
      }
      check_website_parent
    """,
    )
    assert "unexpected-mutation" not in result.stdout
    if writable:
        assert result.returncode != 0
        assert "sudo chmod go-w /var/www" in result.stderr
        assert "No permissions were changed automatically" in result.stderr
    else:
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("answer", ["k\n", "keep\n"])
def test_uninstall_keep_choice_never_runs_restoration(answer):
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      list_website_links() { printf 'site\texample.com\n'; }
      sudo() { echo unexpected-privileged-operation; return 1; }
      restore_websites
    """,
        stdin=answer,
    )
    assert result.returncode == 0
    assert "unexpected-privileged-operation" not in result.stdout


@pytest.mark.parametrize("answer", ["c\n", "\n", ""])
def test_uninstall_website_cancellation_is_safe(answer):
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      list_website_links() { printf 'site\texample.com\n'; }
      sudo() { echo unexpected-privileged-operation; return 1; }
      restore_websites
      echo unsafe-continuation
    """,
        stdin=answer,
    )
    assert result.returncode != 0
    assert "unexpected-privileged-operation" not in result.stdout
    assert "unsafe-continuation" not in result.stdout


def test_uninstall_restores_only_confirmed_sites_and_stops_on_error():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      list_website_links() { printf 'one\tone.example\ntwo\ttwo.example\n'; }
      sudo() { if [[ $1 == test ]]; then return 0; fi; echo "$*"; return 1; }
      restore_websites
      echo unsafe-continuation
    """,
        stdin="r\n",
    )
    assert result.returncode != 0
    assert "connect-website detach one one.example" in result.stdout
    assert "connect-website detach two two.example" not in result.stdout
    assert "unsafe-continuation" not in result.stdout


def run_function(script, body, stdin=""):
    return subprocess.run(
        ["bash", "-c", 'source "$1"; ' + body, "test", str(DEPLOY / script)],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("script", ["install-ubuntu.sh", "uninstall-ubuntu.sh", "update-ubuntu.sh"])
def test_bash_syntax(script):
    assert subprocess.run(["bash", "-n", str(DEPLOY / script)], check=False).returncode == 0


def test_nginx_poll_route_matches_actual_deployment_ids(tmp_path):
    from deployd.store.db import Store

    store = Store(tmp_path / "test.sqlite3")
    store.init()
    deploy_id = store.create_deploy("site", "a" * 40, "https://example.com/a.zip", "b" * 64, "test")
    output = tmp_path / "nginx.conf"
    result = run_function(
        "install-ubuntu.sh",
        f'render_nginx_config "{output}" /opt/deployd deployd.example.com 127.0.0.1 844',
    )
    assert result.returncode == 0, result.stderr
    pattern = re.search(r'location ~\* "([^"]+)"', output.read_text()).group(1)
    assert re.fullmatch(pattern, f"/deploys/{deploy_id}")
    assert not re.fullmatch(pattern, "/admin/apps")
    assert not re.fullmatch(pattern, "/deploys/invalid")


@pytest.mark.parametrize(
    "port", ["0", "65536", "80", "00080", "00445", "08300", "8+1", "99999999999"]
)
def test_rejects_invalid_or_conflicting_management_ports(port):
    result = run_function("install-ubuntu.sh", f'validate_port "{port}"')
    assert result.returncode != 0


@pytest.mark.parametrize("port", ["844", "00844", "8302", "65535"])
def test_accepts_decimal_management_ports(port):
    assert run_function("install-ubuntu.sh", f'validate_port "{port}"').returncode == 0


def test_install_does_not_continue_when_service_stop_fails():
    result = run_function(
        "install-ubuntu.sh",
        """
      systemctl() { return 0; }
      sudo() { return 1; }
      stop_for_upgrade
      echo unsafe-continuation
    """,
    )
    assert result.returncode != 0 and "unsafe-continuation" not in result.stdout


@pytest.mark.parametrize("active,action", [(0, "reload"), (3, "start")])
def test_installer_starts_nginx_when_inactive_and_reloads_when_running(active, action):
    result = run_function(
        "install-ubuntu.sh",
        f"""
      systemctl() {{ return {active}; }}
      sudo() {{ printf '%s\\n' "$*"; }}
      activate_nginx
    """,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"systemctl {action} nginx"


def test_uninstall_aborts_before_removal_when_stop_fails():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      sudo() { if [[ $1 == test ]]; then return 0; fi; return 1; }
      stop_service_safely
      echo unsafe-continuation
    """,
    )
    assert result.returncode != 0 and "nothing was removed" in result.stderr
    assert "unsafe-continuation" not in result.stdout


def test_uninstall_accepts_no_remaining_processes():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      systemctl() { return 3; }
      id() { return 0; }
      sudo() { case "$1" in test|pgrep) return 1;; *) return 0;; esac; }
      stop_service_safely
    """,
    )
    assert result.returncode == 0, result.stderr


def test_uninstall_rejects_remaining_processes():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      systemctl() { return 3; }
      id() { return 0; }
      sudo() { case "$1" in test) return 1;; pgrep) echo 123; return 0;; *) return 0;; esac; }
      stop_service_safely
    """,
    )
    assert result.returncode != 0 and "running processes" in result.stderr


def test_uninstall_preserves_service_identity_with_retained_data():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      preserve_identity=true
      sudo() { echo unsafe-command; return 1; }
      remove_service_identity
    """,
    )
    assert result.returncode == 0 and "unsafe-command" not in result.stdout


def test_uninstall_requires_exact_confirmation():
    assert (
        run_function("uninstall-ubuntu.sh", "confirm_removal", "remove deployd\n").returncode != 0
    )
    assert (
        run_function("uninstall-ubuntu.sh", "confirm_removal", "REMOVE deployd\n").returncode == 0
    )


def test_uninstall_refuses_unexpected_tree_target():
    result = run_function("uninstall-ubuntu.sh", "remove_tree /tmp/not-deployd /opt/deployd")
    assert result.returncode != 0 and "unexpected removal target" in result.stderr


def test_prompts_honour_environment_overrides():
    result = run_function(
        "install-ubuntu.sh",
        'DEPLOYD_INSTALL_DOMAIN=env.example.com prompt_default DEPLOYD_INSTALL_DOMAIN "Domain" "x.example"',
    )
    assert result.stdout == "env.example.com"
    result = run_function(
        "install-ubuntu.sh",
        'prompt_default DEPLOYD_INSTALL_DOMAIN "Domain" "x.example"',
        stdin="\n",
    )
    assert result.stdout == "x.example"


SITE = """server {
    listen 80;
    server_name deploy.example.com;
}
server {
    listen 10.0.0.5:9443;
    server_name _;
}
"""


def test_installed_settings_are_read_from_the_nginx_site():
    result = run_function(
        "install-ubuntu.sh",
        "SITE=$(cat <<'NGINX'\n" + SITE + "NGINX\n)\n"
        'sudo() { case "$1" in test) return 0 ;; cat) printf "%s" "$SITE" ;; esac; }\n'
        "installed_settings",
    )
    assert result.stdout.split() == ["deploy.example.com", "10.0.0.5", "9443"]
    missing = run_function("install-ubuntu.sh", "sudo() { return 1; }; installed_settings")
    assert missing.returncode != 0 and missing.stdout == ""


@pytest.mark.parametrize(
    ("env", "stdin", "expected"),
    [
        ("", "", "update"),
        ("", "\n", "update"),
        ("", "y\n", "update"),
        ("", "n\n", "reconfigure"),
        ("DEPLOYD_INSTALL_MODE=update", "n\n", "update"),
        ("DEPLOYD_INSTALL_MODE=reconfigure", "", "reconfigure"),
    ],
)
def test_existing_install_defaults_to_in_place_update(env, stdin, expected):
    result = run_function(
        "install-ubuntu.sh", f"{env} choose_mode deploy.example.com 127.0.0.1 844", stdin=stdin
    )
    assert result.stdout.strip() == expected
    assert "Existing installation found" in result.stderr or env


def test_update_flag_sets_mode_and_rejects_unknown_arguments():
    assert (
        run_function(
            "install-ubuntu.sh", "parse_args --update; echo $DEPLOYD_INSTALL_MODE"
        ).stdout.strip()
        == "update"
    )
    assert (
        run_function(
            "install-ubuntu.sh", "parse_args --reconfigure; echo $DEPLOYD_INSTALL_MODE"
        ).stdout.strip()
        == "reconfigure"
    )
    assert run_function("install-ubuntu.sh", "parse_args --bogus").returncode != 0
    assert (
        run_function("install-ubuntu.sh", "DEPLOYD_INSTALL_MODE=weird parse_args").returncode != 0
    )


def test_apt_update_runs_only_when_a_package_is_missing():
    result = run_function(
        "install-ubuntu.sh",
        """
      dpkg-query() { echo installed; }
      sudo() { echo "unexpected: $*"; return 1; }
      apt_install_missing
    """,
    )
    assert result.returncode == 0 and "unexpected" not in result.stdout
    result = run_function(
        "install-ubuntu.sh",
        """
      dpkg-query() { [[ ${@: -1} == nginx ]] && return 1; echo installed; }
      sudo() { echo "$*"; }
      apt_install_missing
    """,
    )
    assert result.stdout.splitlines() == ["apt-get update", "apt-get install -y nginx"]


@pytest.mark.parametrize(
    "version,ok", [("v22.19.0", True), ("v24.1.0", True), ("v22.18.9", False), ("v20.19.0", False)]
)
def test_node_minimum_version(tmp_path, version, ok):
    node = tmp_path / "node"
    node.write_text(f"#!/bin/sh\necho {version}\n")
    node.chmod(0o755)
    result = run_function("install-ubuntu.sh", f'node_meets_minimum "{node}"')
    assert (result.returncode == 0) is ok
    assert run_function("install-ubuntu.sh", 'node_meets_minimum ""').returncode != 0


def test_port_preflight_accepts_nginx_and_rejects_other_listeners():
    result = run_function(
        "install-ubuntu.sh",
        """
      systemctl() { return 0; }
      sudo() { printf 'LISTEN 0 511 0.0.0.0:%s 0.0.0.0:* users:(("nginx",pid=1,fd=6))\\n' "${@: -1}"; }
      check_ports 844
    """,
    )
    assert result.returncode == 0, result.stderr
    result = run_function(
        "install-ubuntu.sh",
        """
      systemctl() { return 0; }
      sudo() { [[ $* == *:844 ]] && printf 'LISTEN 0 5 0.0.0.0:844 0.0.0.0:* users:(("python3",pid=9,fd=3))\\n'; return 0; }
      check_ports 844
    """,
    )
    assert result.returncode != 0 and "port 844 is used" in result.stderr


def test_upgrade_waits_for_active_deploys_and_refuses_when_declined():
    body = """
      systemctl() { return 0; }
      sudo() { return 0; }
      sleep() { :; }
      counter=$(mktemp)
      pending_deploys() { echo . >>"$counter"; if (($(wc -l <"$counter") < 3)); then echo 2; else echo 0; fi; }
      wait_for_idle_queue /var/lib/deployd/deployd.sqlite3
      echo "polls=$(wc -l <"$counter" | tr -d ' ')"
      rm -f "$counter"
    """
    result = run_function("install-ubuntu.sh", "DEPLOYD_INSTALL_YES=1\n" + body)
    assert result.returncode == 0 and "polls=3" in result.stdout
    result = run_function("install-ubuntu.sh", body, stdin="n\n")
    assert result.returncode != 0 and "cancelled" in result.stderr
    result = run_function(
        "install-ubuntu.sh",
        """
      systemctl() { return 0; }
      sudo() { return 0; }
      sleep() { :; }
      pending_deploys() { echo 1; }
      DEPLOYD_INSTALL_YES=1 DEPLOYD_INSTALL_WAIT_SECONDS=10 wait_for_idle_queue /db
      echo unsafe-continuation
    """,
    )
    assert result.returncode != 0 and "still active" in result.stderr
    assert "unsafe-continuation" not in result.stdout


def test_nginx_config_hardens_api_location_and_rate_limits_deploys(tmp_path):
    output = tmp_path / "nginx.conf"
    result = run_function(
        "install-ubuntu.sh",
        f'render_nginx_config "{output}" /opt/deployd deployd.example.com 127.0.0.1 844',
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text()
    assert "limit_req_zone $deployd_client_ip zone=deployd_deploys:1m rate=10r/m;" in text
    deploys = text[text.index("location = /deploys {") : text.index("location ~*")]
    assert "limit_req zone=deployd_deploys burst=5 nodelay;" in deploys
    api = text[text.index("location /api/ {") : text.rindex("location / {")]
    assert "proxy_set_header X-Remote-User $remote_user;" in api
    assert 'add_header X-Frame-Options "DENY" always;' in api
    assert "Content-Security-Policy" in api
    assert text.count('add_header X-Content-Type-Options "nosniff" always;') == 2


def test_prune_keeps_newest_files_by_name(tmp_path):
    for stamp in ["20240101", "20240102", "20240103", "20240104", "20240105"]:
        (tmp_path / f"deployd.backup.{stamp}").write_text("x")
    (tmp_path / "other.conf").write_text("keep")
    result = run_function(
        "install-ubuntu.sh",
        f"""
      sudo() {{ "$@"; }}
      prune_privileged_files "{tmp_path}" 3 'deployd.backup.*'
    """,
    )
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "deployd.backup.20240103",
        "deployd.backup.20240104",
        "deployd.backup.20240105",
        "other.conf",
    ]


def test_uninstall_parses_purge_flag():
    result = run_function("uninstall-ubuntu.sh", 'parse_args --purge; echo "purge=$purge"')
    assert result.returncode == 0 and "purge=true" in result.stdout
    assert run_function("uninstall-ubuntu.sh", "parse_args --bogus").returncode != 0
    assert (
        run_function("uninstall-ubuntu.sh", 'parse_args; echo "purge=$purge"').stdout.strip()
        == "purge=false"
    )


def test_uninstall_continues_when_nginx_was_already_broken_by_another_site():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      nginx() { return 1; }
      systemctl() { return 3; }
      sudo() { case "$1" in nginx|test) return 1 ;; cp) echo restored; return 1 ;; *) return 0 ;; esac; }
      remove_nginx_config
      echo continued
    """,
    )
    assert result.returncode == 0, result.stderr
    assert "continued" in result.stdout and "restored" not in result.stdout
    assert "already failing" in result.stderr


def test_uninstall_restores_nginx_when_removal_breaks_a_valid_config():
    result = run_function(
        "uninstall-ubuntu.sh",
        """
      nginx() { return 0; }
      systemctl() { return 3; }
      checks=0
      sudo() {
        case "$1" in
          nginx) checks=$((checks + 1)); [[ $checks -eq 1 ]] ;;
          test) [[ $2 == -e ]] ;;
          cp) echo "restore $*"; return 0 ;;
          *) return 0 ;;
        esac
      }
      remove_nginx_config
      echo unsafe-continuation
    """,
    )
    assert result.returncode != 0 and "unsafe-continuation" not in result.stdout
    assert "restore" in result.stdout and "uninstall stopped" in result.stderr


@pytest.mark.parametrize(
    "content,configured",
    [
        ("apps: {}\n", False),
        ("apps:\n", False),
        ("# only comments\napps: {}\n", False),
        ("apps:\n  site:\n    releases_dir: /x\n", True),
    ],
)
def test_uninstall_detects_configured_apps_without_running_checkout_code(
    tmp_path, content, configured
):
    apps = tmp_path / "apps.yaml"
    apps.write_text(content)
    result = run_function(
        "uninstall-ubuntu.sh",
        f"""
      sudo() {{ if [[ $1 == grep ]]; then shift; set -- "${{@:1:$#-1}}" "{apps}"; grep "$@"; else return 0; fi; }}
      apps_configured
    """,
    )
    assert (result.returncode == 0) is configured


@pytest.mark.parametrize(("mode", "ok"), [("install", True), ("update", False)])
def test_dirty_checkout_blocks_only_updates(tmp_path, mode, ok):
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("")
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text("{}")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    (repo / "pyproject.toml").write_text("changed")
    result = run_function(
        "install-ubuntu.sh",
        f'require_clean_for_update "{repo}" {mode}; echo continued',
    )
    assert ("continued" in result.stdout) is ok
    assert "pyproject.toml" in result.stderr


def test_public_site_forwards_the_cloudflare_visitor_ip(tmp_path):
    output = tmp_path / "site.conf"
    result = run_function(
        "install-ubuntu.sh",
        f'render_nginx_config "{output}" /opt/deployd deployd.example.com 0.0.0.0 844',
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text()
    assert "map $http_cf_connecting_ip $deployd_client_ip" in text
    assert "limit_req_zone $deployd_client_ip" in text
    public = text.split("server {")[1]
    assert public.count("proxy_set_header X-Forwarded-For $deployd_client_ip;") == 3
    management = text.split("server {")[2]
    assert "proxy_add_x_forwarded_for" in management


def test_public_site_listens_on_443_with_the_origin_certificate(tmp_path):
    output = tmp_path / "site.conf"
    result = run_function(
        "install-ubuntu.sh",
        f'render_nginx_config "{output}" /opt/deployd deployd.example.com 0.0.0.0 844',
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text()
    public = text.split("server {")[1]
    assert "listen 443 ssl;" in public and "listen 80;" not in public
    redirect = text.split("server {")[3]
    assert "listen 80;" in redirect and "return 301 https://$host$request_uri;" in redirect
    assert "ssl_certificate /etc/deployd/tls/deployd.crt;" in public
    assert "ssl_certificate_key /etc/deployd/tls/deployd.key;" in public
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in public


def test_management_site_is_tls_only_and_redirects_plain_http_on_its_port(tmp_path):
    output = tmp_path / "site.conf"
    result = run_function(
        "install-ubuntu.sh",
        f'render_nginx_config "{output}" /opt/deployd deployd.example.com 0.0.0.0 844',
    )
    assert result.returncode == 0, result.stderr
    management = output.read_text().split("server {")[2]
    assert "listen 0.0.0.0:844 ssl;" in management
    assert "ssl_certificate /etc/deployd/tls/deployd.crt;" in management
    assert "error_page 497 =301 https://$http_host$request_uri;" in management
    assert "client_max_body_size 512k;" in management
    assert "auth_basic_user_file" in management


def test_installed_settings_survive_the_tls_listen_flag():
    site = SITE.replace("listen 10.0.0.5:9443;", "listen 10.0.0.5:9443 ssl;")
    result = run_function(
        "install-ubuntu.sh",
        "SITE=$(cat <<'NGINX'\n" + site + "NGINX\n)\n"
        'sudo() { case "$1" in test) return 0 ;; cat) printf "%s" "$SITE" ;; esac; }\n'
        "installed_settings",
    )
    assert result.stdout.split() == ["deploy.example.com", "10.0.0.5", "9443"]


def test_env_dropin_grants_write_access_only_where_the_sandbox_blocks_it():
    nested = run_function(
        "install-ubuntu.sh", "render_env_dropin /opt /srv/apps /etc/globalgo /usr/local/etc"
    )
    assert nested.returncode == 0, nested.stderr
    assert "ReadWritePaths=-/etc/globalgo -/usr/local/etc\n" in nested.stdout
    assert (
        "ProtectSystem" not in nested.stdout and "/opt" not in nested.stdout.split("[Service]")[1]
    )
    # systemd keeps /etc read-only when it is listed as both, so /etc lifts the protection.
    whole = run_function("install-ubuntu.sh", "render_env_dropin /etc /opt /usr")
    assert "ProtectSystem=yes\n" in whole.stdout and "ReadWritePaths" not in whole.stdout
    assert "/usr stays read-only" in whole.stderr
    empty = run_function("install-ubuntu.sh", "render_env_dropin /opt")
    assert empty.stdout.strip().endswith("[Service]")


def test_env_editor_is_opt_in_and_declining_installs_nothing():
    result = run_function(
        "install-ubuntu.sh",
        """
      sudo() { case "$1" in test) return 1 ;; *) echo unexpected-privileged-operation ;; esac; }
      install_env_helper /opt/deployd
      echo "status=$?"
    """,
        stdin="n\n",
    )
    assert "status=0" in result.stdout
    assert "unexpected-privileged-operation" not in result.stdout
    assert "view and edit app .env files" in result.stdout


def test_env_roots_default_to_the_apps_env_folder_and_accept_additions(tmp_path):
    roots = tmp_path / "env-roots"
    body = f"""
      sudo() {{
        case "$1" in
          install) [[ $2 == -d ]] || cat >"${{@: -1}}" ;;
          *) "$@" ;;
        esac
      }}
      configure_env_roots "{roots}"
      DEPLOYD_INSTALL_ENV_ROOTS=/srv/apps:/etc configure_env_roots "{roots}"
      env_roots "{roots}"
      DEPLOYD_INSTALL_ENV_ROOTS=relative configure_env_roots "{roots}"
    """
    result = run_function("install-ubuntu.sh", body)
    assert result.stdout.split() == ["/etc/apps-env", "/srv/apps", "/etc"]
    assert result.returncode != 0 and "must be an absolute path" in result.stderr


def test_unsafe_env_directory_warns_without_stopping_the_install():
    result = run_function(
        "install-ubuntu.sh",
        """
      sudo() {
        case "$1" in
          test) [[ $2 == -d ]] ;;
          stat) echo root ;;
          find) echo /srv/shared ;;
        esac
      }
      check_env_roots /srv/shared
      echo continued
    """,
    )
    assert result.returncode == 0 and "continued" in result.stdout
    assert "/srv/shared is not a root-owned directory" in result.stderr
