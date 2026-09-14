import json
import subprocess
from types import SimpleNamespace

import pytest

from deployd import account


@pytest.fixture
def helper(tmp_path, monkeypatch):
    path = tmp_path / "helper"
    path.touch()
    monkeypatch.setattr(account, "HELPER", path)
    monkeypatch.setattr(account, "_next_attempt", 0)
    return path


def test_passwords_only_passed_through_stdin(helper, monkeypatch):
    def run(args, **kwargs):
        assert args == ["/usr/bin/sudo", "-n", str(helper)]
        assert json.loads(kwargs["input"]) == {
            "username": "dan",
            "current_password": "old",
            "new_password": "new",
        }
        assert kwargs["stderr"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(account.subprocess, "run", run)
    account.change_password("dan", "old", "new")
    with pytest.raises(account.PasswordError) as exc:
        account.change_password("dan", "old", "new")
    assert exc.value.status == 429


@pytest.mark.parametrize("returncode,status", [(3, 400), (1, 503)])
def test_helper_failures_are_sanitized(helper, monkeypatch, returncode, status):
    monkeypatch.setattr(
        account.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=returncode)
    )
    with pytest.raises(account.PasswordError) as exc:
        account.change_password("dan", "secret-current", "secret-new")
    assert exc.value.status == status
    assert "secret" not in str(exc.value)


def test_missing_helper_explains_upgrade(helper):
    helper.unlink()
    with pytest.raises(account.PasswordError) as exc:
        account.change_password("dan", "old", "new")
    assert exc.value.status == 503
    assert "installer" in str(exc.value)


def test_timeout_does_not_leak_stdin_and_releases_lock(helper, monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 25, output=kwargs["input"])

    monkeypatch.setattr(account.subprocess, "run", timeout)
    with pytest.raises(account.PasswordError) as exc:
        account.change_password("dan", "secret-current", "secret-new")
    assert exc.value.status == 503
    assert "secret" not in str(exc.value)
    assert not account._lock.locked()
