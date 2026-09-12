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
    with pytest.raises(ValueError, match="unsafe permissions 0775"):
        run()


def test_permissions_error_identifies_parent_and_never_changes_it(site):
    original, current, _ = site
    original.parent.chmod(0o2775)
    with pytest.raises(ValueError, match="sudo chmod go-w /var/www"):
        run()
    assert original.parent.stat().st_mode & 0o7777 == 0o2775
    assert (current / "index.html").read_text() == "new website"
    original.parent.chmod(0o2755)
    assert run()["status"] == "ready"


def test_reconnect_requires_our_restore_receipt(site):
    original, _, state = site
    transaction = state / "site"
    transaction.mkdir(mode=0o700)
    (transaction / "journal.json").write_text(json.dumps({"site": original.name}))
    (transaction / "complete.json").write_text("{}")
    with pytest.raises(ValueError, match="no matching restore receipt"):
        run("connect")
    assert not original.is_symlink()


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
def test_reconnect_preserves_edits_and_baseline_across_multiple_removals(site):
    original, current, state = site
    run("connect")
    for cycle in range(2):
        assert detach()["status"] == "restored"
        (original / "index.html").write_text(f"restored edit {cycle}")
        assert run() == {"status": "reconnect", "backup": True}
        assert not original.is_symlink()
        assert run("connect")["status"] == "connected"
        assert run("connect")["status"] == "connected"
        assert original.resolve() == current
    assert detach()["status"] == "restored"
    saved = list((state / "site").glob("saved-*/reconnected-files/index.html"))
    assert {path.read_text() for path in saved} == {"restored edit 0", "restored edit 1"}
    assert (original / "index.html").read_text() == "new website"
    assert (current.parent / "b4deployd/index.html").read_text() == "original website"


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
@pytest.mark.parametrize("after", [False, True])
def test_reconnect_recovers_interrupted_exchange(site, monkeypatch, after):
    original, current, state = site
    run("connect")
    detach()
    (original / "index.html").write_text("restored edit")
    native = helper.rename

    def interrupted(*args):
        if after:
            native(*args)
        raise SystemExit("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(helper, "rename", interrupted)
        with pytest.raises(SystemExit):
            run("connect")
    assert run("connect")["status"] == "connected"
    assert original.resolve() == current
    assert (
        state / "site/restore-example.com/reconnected-files/index.html"
    ).read_text() == "restored edit"
    assert detach()["status"] == "restored"


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
def test_reconnect_refuses_a_replacement_directory(site):
    original, _, _ = site
    run("connect")
    detach()
    original.rename(original.with_suffix(".saved"))
    original.mkdir()
    (original / "index.html").write_text("unrelated site")
    with pytest.raises(ValueError, match="changed after restore"):
        run("connect")
    assert (original / "index.html").read_text() == "unrelated site"


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


def detach():
    return helper.disconnect("site", "example.com", (os.getuid(), os.getgid()))


def test_detach_leaves_unconnected_real_directory_untouched(site):
    original, _, _ = site
    assert detach()["status"] == "unlinked"
    assert (original / "index.html").read_text() == "original website"


def test_detach_refuses_foreign_link(site):
    original, _, _ = site
    backup = original.with_suffix(".bak")
    original.rename(backup)
    original.symlink_to(backup)
    with pytest.raises(ValueError, match="not this app's symlink"):
        detach()
    assert original.resolve() == backup


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
def test_detach_restores_current_not_baseline_and_keeps_releases(site):
    original, current, _ = site
    run("connect")
    assert detach()["status"] == "restored"
    assert original.is_dir() and not original.is_symlink()
    assert (original / "index.html").read_text() == "new website"
    assert not (original / helper.MANIFEST).exists()
    assert (current / "index.html").read_text() == "new website"
    assert (current.parent / "b4deployd/index.html").read_text() == "original website"
    assert detach()["status"] == "restored"


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
@pytest.mark.parametrize("phase", ["copy", "journal", "exchange"])
def test_interrupted_detach_is_safe_and_retryable(site, monkeypatch, phase):
    original, current, _ = site
    run("connect")
    copy, rename = helper.copy_site, helper.rename

    def fail_copy(*args, **kwargs):
        copy(*args, **kwargs)
        if phase == "copy":
            raise SystemExit("interrupted copy")

    def fail_rename(*args):
        rename(*args)
        if (phase == "journal" and args[-1] == 1) or (phase == "exchange" and args[-1] == 2):
            raise SystemExit("interrupted exchange")

    monkeypatch.setattr(helper, "copy_site", fail_copy)
    monkeypatch.setattr(helper, "rename", fail_rename)
    with pytest.raises(SystemExit):
        detach()
    assert (original / "index.html").read_text() == "new website"
    assert (current / "index.html").read_text() == "new website"
    monkeypatch.setattr(helper, "copy_site", copy)
    monkeypatch.setattr(helper, "rename", rename)
    assert detach()["status"] == "restored"
    assert not original.is_symlink()


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux renameat2 exchange")
def test_changed_live_version_cannot_be_silently_rolled_back_by_retry(site, monkeypatch):
    original, current, _ = site
    run("connect")
    rename = helper.rename

    def stop_after_journal(*args):
        rename(*args)
        raise SystemExit("before exchange")

    monkeypatch.setattr(helper, "rename", stop_after_journal)
    with pytest.raises(SystemExit):
        detach()
    monkeypatch.setattr(helper, "rename", rename)
    (current / helper.MANIFEST).write_text(json.dumps({"name": "c" * 40 + "-" + "d" * 32}))
    with pytest.raises(ValueError, match="live version changed"):
        detach()
    assert original.is_symlink()
