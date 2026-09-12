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


@pytest.mark.parametrize("script", ["install-ubuntu.sh", "uninstall-ubuntu.sh"])
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
