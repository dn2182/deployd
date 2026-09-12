import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


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
