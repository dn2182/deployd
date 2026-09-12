import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "deploy/connect_website.py"
module = importlib.util.spec_from_file_location("connect_helper", source)
helper = importlib.util.module_from_spec(module)
module.loader.exec_module(helper)


@pytest.fixture
def site(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    web, releases, state = base / "www", base / "managed", base / "state"
    web.mkdir(mode=0o755)
    state.mkdir(mode=0o700)
    current = releases / "site/releases/current"
    current.mkdir(parents=True, mode=0o755)
    (current / "index.html").write_text("new website")
    (current / helper.MANIFEST).write_text(json.dumps({"name": "a" * 40 + "-" + "b" * 32}))
    original = web / "example.com"
    original.mkdir(mode=0o755)
    (original / "index.html").write_text("original website")
    monkeypatch.setattr(helper, "WEB_ROOT", web)
    monkeypatch.setattr(helper, "RELEASE_ROOT", releases)
    monkeypatch.setattr(helper, "STATE_ROOT", state)
    monkeypatch.setattr(helper, "ROOT_UID", os.getuid())
    return original, current, state


def run(operation="check", app="site", name="example.com"):
    return helper.connect(operation, app, name, (os.getuid(), os.getgid()))


def test_check_does_not_move_website_or_create_transaction(site):
    original, current, state = site
    assert run() == {"status": "ready", "backup": False}
    assert not original.is_symlink()
    assert (original / "index.html").read_text() == "original website"
    assert not (state / "site").exists()
    assert not (current.parent / "b4deployd").exists()


@pytest.mark.parametrize("name", ["../outside", ".", "..", "/etc", "--help", "a/b", "a\nb"])
def test_rejects_unapproved_names(site, name):
    with pytest.raises(ValueError):
        run(name=name)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "private", "metadata"])
def test_rejects_unsafe_original_without_switching(site, kind):
    original, current, state = site
    entry = original / "unsafe"
    if kind == "symlink":
        entry.symlink_to(current)
    elif kind == "hardlink":
        os.link(original / "index.html", entry)
    elif kind == "fifo":
        os.mkfifo(entry)
    elif kind == "private":
        (original / "index.html").chmod(0o600)
    else:
        (original / helper.MANIFEST).write_text("{}")
    with pytest.raises(ValueError):
        run("connect")
    assert not original.is_symlink()
    assert not (state / "site").exists()


def test_requires_readable_current_and_protected_parent(site):
    original, current, _ = site
    current.chmod(0o700)
    with pytest.raises(ValueError, match="accessible to Nginx"):
        run()
    current.chmod(0o755)
    original.parent.chmod(0o775)
    with pytest.raises(ValueError, match="root-owned and protected"):
        run()


def test_already_linked_does_not_invent_a_backup(site):
    original, current, state = site
    original.rename(original.with_suffix(".bak"))
    original.symlink_to(current)
    assert run("connect") == {"status": "connected", "backup": False}
    assert not (state / "site").exists()


def test_different_symlink_is_never_replaced(site):
    original, current, _ = site
    backup = original.with_suffix(".bak")
    original.rename(backup)
    original.symlink_to(backup)
    with pytest.raises(ValueError, match="different symlink"):
        run("connect")
    assert original.resolve() == backup
    assert (current / "index.html").read_text() == "new website"


def test_backup_is_never_overwritten(site):
    original, current, _ = site
    (current.parent / "b4deployd").mkdir()
    with pytest.raises(ValueError, match="overwrite"):
        run("connect")
    assert not original.is_symlink()


def test_rejects_nested_bind_mount_even_on_same_device(site, monkeypatch):
    original, _, state = site
    nested = original / "mounted"
    nested.mkdir()
    mounted_inode = nested.stat().st_ino
    actual = helper.mount_id
    monkeypatch.setattr(
        helper, "mount_id", lambda fd: -1 if os.fstat(fd).st_ino == mounted_inode else actual(fd)
    )
    with pytest.raises(ValueError, match="mount"):
        run("connect")
    assert not original.is_symlink()
    assert not (state / "site").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
def test_connect_and_repeated_connection_preserve_original(site):
    original, current, _ = site
    before = (original / "index.html").stat()
    assert run("connect") == {"status": "connected", "backup": True}
    assert original.is_symlink() and original.resolve() == current
    backup = current.parent / "b4deployd"
    assert (backup / "index.html").read_text() == "original website"
    after = (backup / "index.html").stat()
    assert (before.st_ino, before.st_uid, before.st_mode) == (
        after.st_ino,
        after.st_uid,
        after.st_mode,
    )
    assert json.loads((backup / helper.MANIFEST).read_text())["name"] == "b4deployd"
    assert backup.stat().st_mode & 0o777 == 0o755
    assert run("connect") == {"status": "connected", "backup": True}


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
@pytest.mark.parametrize("crash_at", ["exchange", "archive", "manifest"])
def test_retry_recovers_after_interrupted_connection(site, monkeypatch, crash_at):
    original, current, state = site
    rename, write = helper.rename, helper.write_json

    def interrupted_rename(*args):
        rename(*args)
        if args[-1] == (2 if crash_at == "exchange" else 1) and crash_at != "manifest":
            raise SystemExit("simulated crash")

    def interrupted_write(*args):
        write(*args)
        if args[1] == helper.MANIFEST and crash_at == "manifest":
            raise SystemExit("simulated crash")

    monkeypatch.setattr(helper, "rename", interrupted_rename)
    monkeypatch.setattr(helper, "write_json", interrupted_write)
    with pytest.raises(SystemExit):
        run("connect")
    assert run("check")["status"] == "recovery"
    assert original.resolve() == current
    candidates = [state / "site/original/index.html", current.parent / "b4deployd/index.html"]
    assert any(p.exists() and p.read_text() == "original website" for p in candidates)
    monkeypatch.setattr(helper, "rename", rename)
    monkeypatch.setattr(helper, "write_json", write)
    assert run("connect")["status"] == "connected"
    assert (current.parent / "b4deployd/index.html").read_text() == "original website"
