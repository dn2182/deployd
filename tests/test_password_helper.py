import importlib.util
import os
import shutil
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "deploy/change_password.py"
spec = importlib.util.spec_from_file_location("password_helper", source)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    executable = shutil.which("htpasswd")
    if not executable:
        pytest.skip("htpasswd is not installed")
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o750)
    path = directory / "htpasswd"
    path.touch(mode=0o640)
    monkeypatch.setattr(helper, "AUTH_DIR", directory)
    monkeypatch.setattr(helper, "PASSWORD_FILE", path)
    monkeypatch.setattr(helper, "ROOT_UID", os.getuid())
    monkeypatch.setattr(helper, "HTPASSWD", executable)
    protected = helper.protected
    monkeypatch.setattr(
        helper,
        "protected",
        lambda p, directory=False: (
            protected(p, directory) if p.is_relative_to(tmp_path) else p.stat()
        ),
    )
    assert helper.htpasswd(path, "dan", "original-password") == 0
    assert helper.htpasswd(path, "other", "other-password") == 0
    return path


def payload(**changes):
    return {
        "username": "dan",
        "current_password": "original-password",
        "new_password": "replacement-password",
        **changes,
    }


def test_password_changes_atomically_and_preserves_other_users(credentials):
    other = credentials.read_text().splitlines()[1]
    previous_inode = credentials.stat().st_ino
    assert helper.change(payload()) == 0
    assert credentials.stat().st_ino != previous_inode
    assert credentials.stat().st_mode & 0o777 == 0o640
    assert helper.htpasswd(credentials, "dan", "replacement-password", verify=True) == 0
    assert helper.htpasswd(credentials, "dan", "original-password", verify=True) == 3
    assert credentials.read_text().splitlines()[1] == other
    assert not list(credentials.parent.glob(".password-*"))


@pytest.mark.parametrize(
    "changes",
    [
        {"current_password": "incorrect"},
        {"username": "missing"},
    ],
)
def test_wrong_password_or_unknown_user_keeps_file(credentials, changes):
    before = credentials.read_bytes()
    assert helper.change(payload(**changes)) == 3
    assert credentials.read_bytes() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"new_password": "short"},
        {"new_password": "é" * 37},
        {"new_password": "original-password"},
        {"new_password": "bad\npassword-long"},
        {"username": "../other"},
        {"current_password": ""},
    ],
)
def test_rejects_unsafe_passwords_before_mutation(credentials, changes):
    before = credentials.read_bytes()
    with pytest.raises(ValueError):
        helper.change(payload(**changes))
    assert credentials.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable"])
def test_rejects_unsafe_credential_file(credentials, kind):
    if kind == "writable":
        credentials.chmod(0o660)
    else:
        original = credentials.with_name("original")
        credentials.rename(original)
        if kind == "symlink":
            credentials.symlink_to(original)
        else:
            os.link(original, credentials)
    with pytest.raises(ValueError):
        helper.change(payload())


def test_failed_write_preserves_original(credentials, monkeypatch):
    before = credentials.read_bytes()
    real = helper.htpasswd
    monkeypatch.setattr(
        helper,
        "htpasswd",
        lambda path, user, password, verify=False: (
            real(path, user, password, verify=True) if verify else 1
        ),
    )
    with pytest.raises(RuntimeError):
        helper.change(payload())
    assert credentials.read_bytes() == before
    assert not list(credentials.parent.glob(".password-*"))


def test_protected_path_rejects_other_owner(credentials, monkeypatch):
    monkeypatch.setattr(helper, "ROOT_UID", os.getuid() + 1)
    with pytest.raises(ValueError):
        helper.change(payload())
