import importlib.util
import os
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "deploy/env_file.py"
spec = importlib.util.spec_from_file_location("env_helper", source)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

CONTENT = "# database\nDB_URL=postgres://localhost/app\nexport API_KEY='abc'\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    (root / "api").mkdir(parents=True)
    # Explicit modes: the helper refuses a group-writable root, whatever the umask is.
    root.chmod(0o755)
    (root / "api").chmod(0o755)
    roots = tmp_path / "env-roots"
    roots.write_text(f"# allowed\n{root}\n")
    roots.chmod(0o644)
    monkeypatch.setattr(helper, "ENV_ROOTS_FILE", roots)
    monkeypatch.setattr(helper, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(helper, "ROOT_UID", os.getuid())
    path = root / "api" / "api.env"
    path.write_text(CONTENT)
    path.chmod(0o640)
    return path


def write(path, content, expected=None):
    return helper.run("write", "api", str(path), {"content": content, "expected_sha256": expected})


def test_read_returns_content_metadata_and_digest(env):
    result = helper.run("read", "api", str(env))
    assert result["content"] == CONTENT
    assert result["mode"] == "0640" and result["size"] == len(CONTENT)
    assert result["sha256"] == helper.digest(CONTENT.encode())
    check = helper.run("check", "api", str(env))
    assert check["exists"] and check["backups"] == 0 and "content" not in check


def test_write_is_atomic_keeps_mode_and_reports_key_names_only(env):
    before = helper.run("read", "api", str(env))
    inode = env.stat().st_ino
    result = write(env, "DB_URL=postgres://db/app\nNEW=1\n", before["sha256"])
    assert result["saved"] and result["backups"] == 1
    assert (result["added"], result["removed"], result["changed"]) == (
        ["NEW"],
        ["API_KEY"],
        ["DB_URL"],
    )
    assert "postgres" not in str(result)
    assert env.read_text() == "DB_URL=postgres://db/app\nNEW=1\n"
    assert env.stat().st_ino != inode and env.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in env.parent.iterdir()] == ["api.env"]
    stored = list((helper.STATE_ROOT / "api").rglob("*.env"))
    assert len(stored) == 1 and stored[0].read_text() == CONTENT
    assert stored[0].stat().st_mode & 0o777 == 0o600


def test_write_detects_a_file_changed_since_it_was_loaded(env):
    stale = helper.run("read", "api", str(env))["sha256"]
    env.write_text("EDITED=on-server\n")
    with pytest.raises(helper.Conflict):
        write(env, "MINE=1\n", stale)
    assert env.read_text() == "EDITED=on-server\n"


def test_unchanged_content_writes_nothing(env):
    inode = env.stat().st_ino
    result = write(env, CONTENT)
    assert not result["saved"] and result["backups"] == 0
    assert env.stat().st_ino == inode


def test_restore_swaps_in_the_previous_version_and_can_be_undone(env):
    write(env, "A=1\n")
    assert helper.run("restore", "api", str(env))["saved"]
    assert env.read_text() == CONTENT
    helper.run("restore", "api", str(env))
    assert env.read_text() == "A=1\n"


def test_restore_without_history_is_refused(env):
    with pytest.raises(ValueError, match="no earlier version"):
        helper.run("restore", "api", str(env))


def test_only_the_newest_backups_are_kept(env, monkeypatch):
    monkeypatch.setattr(helper, "KEEP_BACKUPS", 2)
    for value in range(4):
        write(env, f"A={value}\n")
    assert helper.run("check", "api", str(env))["backups"] == 2


@pytest.mark.parametrize(
    "content,problem",
    [
        ("not an assignment\n", "line 1"),
        ("A=1\n\n=missing\n", "line 3"),
        ("A=\x00\n", "binary"),
    ],
)
def test_invalid_content_is_rejected_before_touching_the_file(env, content, problem):
    with pytest.raises(ValueError, match=problem):
        write(env, content)
    assert env.read_text() == CONTENT
    assert not (helper.STATE_ROOT / "api").exists() or not list(
        (helper.STATE_ROOT / "api").rglob("*.env")
    )


def test_multiline_quoted_values_and_crlf_are_accepted(env):
    result = write(env, 'KEY="line one\r\nline two"\r\nB=2')
    assert result["added"] == ["B", "KEY"]
    assert env.read_text() == 'KEY="line one\nline two"\nB=2\n'


@pytest.mark.parametrize(
    "name",
    ["shadow", "passwd.txt", "api.env.bak", "..env", "env"],
)
def test_only_env_file_names_are_accepted(env, name):
    target = env.parent / name
    target.write_text("A=1\n")
    with pytest.raises(ValueError, match="named .env"):
        helper.run("read", "api", str(target))


def test_paths_outside_the_allowed_directories_are_refused(env, tmp_path):
    outside = tmp_path / "elsewhere" / "api.env"
    outside.parent.mkdir()
    outside.write_text("A=1\n")
    with pytest.raises(ValueError, match="allowed directory"):
        helper.run("read", "api", str(outside))
    deep = env.parent / "a" / "b" / "c" / "d" / "x.env"
    with pytest.raises(ValueError, match="allowed directory"):
        helper.run("read", "api", str(deep))
    with pytest.raises(ValueError):
        helper.run("read", "api", str(env.parent / ".." / "api" / "api.env"))


def test_deployd_own_files_are_never_editable(env, monkeypatch):
    monkeypatch.setattr(helper, "FORBIDDEN", (env.parent,))
    with pytest.raises(ValueError, match="named .env"):
        helper.run("read", "api", str(env))


def test_symlinks_and_hard_links_are_refused(env, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("ROOT_ONLY=1\n")
    link = env.parent / "link.env"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="symbolic link"):
        helper.run("read", "api", str(link))
    hard = env.parent / "hard.env"
    os.link(secret, hard)
    with pytest.raises(ValueError, match="hard links"):
        helper.run("read", "api", str(hard))
    real = env.parent.parent / "real"
    real.mkdir()
    (real / "x.env").write_text("A=1\n")
    (env.parent.parent / "alias").symlink_to(real)
    with pytest.raises(ValueError, match="not a real directory"):
        helper.run("read", "api", str(env.parent.parent / "alias" / "x.env"))


def test_missing_file_reports_absence_and_is_never_created(env):
    missing = env.parent / "other.env"
    assert helper.run("check", "api", str(missing)) == {"exists": False}
    with pytest.raises(ValueError, match="does not exist"):
        write(missing, "A=1\n")
    assert not missing.exists()


def test_roots_file_must_be_protected_and_present(env):
    helper.ENV_ROOTS_FILE.chmod(0o666)
    with pytest.raises(ValueError, match="root-owned file"):
        helper.run("read", "api", str(env))
    helper.ENV_ROOTS_FILE.unlink()
    with pytest.raises(ValueError, match="enable the .env editor"):
        helper.run("read", "api", str(env))


def test_writable_root_directory_is_refused(env):
    env.parent.parent.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="not writable by others"):
            helper.run("read", "api", str(env))
    finally:
        env.parent.parent.chmod(0o755)


def test_failed_write_leaves_no_stray_backup_and_explains_a_read_only_folder(env, monkeypatch):
    def read_only(*args, **kwargs):
        raise OSError(helper.errno.EROFS, "Read-only file system")

    monkeypatch.setattr(helper, "replace", read_only)
    with pytest.raises(ValueError, match="read-only for the deployd service"):
        write(env, "A=1\n")
    assert env.read_text() == CONTENT
    assert helper.run("check", "api", str(env))["backups"] == 0
