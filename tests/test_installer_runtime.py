import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from deployd.config import get_app_registry, get_settings

_SCRIPT = Path(__file__).parents[1] / "deploy" / "runtime_config.py"
_SPEC = importlib.util.spec_from_file_location("installer_runtime", _SCRIPT)
runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runtime)


@pytest.fixture
def runtime_paths(tmp_path):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    state.mkdir()
    return repo, state


def test_new_environment_uses_service_paths(runtime_paths):
    repo, state = runtime_paths
    token = runtime.prepare(repo, state)
    values = dotenv_values(repo / ".env")
    assert len(token) == 64
    assert values["DEPLOYD_ADMIN_TOKEN"] == token
    assert values["DEPLOYD_DB_PATH"] == str(state / "deployd.sqlite3")
    assert values["DEPLOYD_APPS_CONFIG"] == str(state / "apps.yaml")
    assert values["DEPLOYD_SECRETS_FILE"] == str(state / "secrets.env")
    assert (repo / ".env").stat().st_mode & 0o777 == 0o600


def test_legacy_environment_preserves_secrets_and_backup(runtime_paths):
    repo, state = runtime_paths
    original = (
        "# Existing configuration\n"
        "DEPLOYD_DB_PATH='deployd.sqlite3'\n"
        "DEPLOYD_APPS_CONFIG=config/apps.yaml\n"
        f"DEPLOYD_ADMIN_TOKEN={'a' * 64}\n"
        "DEPLOYD_SECRET_TEST=do-not-change\n"
    )
    (repo / ".env").write_text(original)
    assert runtime.prepare(repo, state) == ""
    values = dotenv_values(repo / ".env")
    assert values["DEPLOYD_ADMIN_TOKEN"] == "a" * 64
    assert values["DEPLOYD_SECRET_TEST"] == "do-not-change"
    assert values["DEPLOYD_APPS_CONFIG"] == str(state / "apps.yaml")
    assert values["DEPLOYD_SECRETS_FILE"] == str(state / "secrets.env")
    backup = next(repo.glob(".env.backup-*"))
    assert backup.read_text() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    updated = (repo / ".env").read_bytes()
    assert runtime.prepare(repo, state) == ""
    assert (repo / ".env").read_bytes() == updated
    assert len(list(repo.glob(".env.backup-*"))) == 1


def test_blank_admin_token_is_generated(runtime_paths):
    repo, state = runtime_paths
    (repo / ".env").write_text("DEPLOYD_ADMIN_TOKEN=\n")
    token = runtime.prepare(repo, state)
    assert len(token) == 64
    assert dotenv_values(repo / ".env")["DEPLOYD_ADMIN_TOKEN"] == token


@pytest.mark.parametrize(
    "existing",
    [
        "deployd.sqlite3",
        "deployd.sqlite3-wal",
        "deployd.sqlite3.lock",
        "config/apps.yaml",
        "config/secrets.env",
    ],
)
def test_legacy_data_requires_migration_without_mutation(runtime_paths, existing):
    repo, state = runtime_paths
    source = repo / existing
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"existing state")
    (repo / ".env").write_text("# preserve me\n")
    with pytest.raises(ValueError, match="existing state"):
        runtime.prepare(repo, state)
    assert source.read_bytes() == b"existing state"
    assert (repo / ".env").read_text() == "# preserve me\n"
    assert not list(repo.glob(".env.backup-*"))


def test_absolute_custom_paths_are_preserved(runtime_paths):
    repo, state = runtime_paths
    custom = repo.parent / "custom"
    (repo / ".env").write_text(f"DEPLOYD_DB_PATH='{custom}/db.sqlite3'\n")
    runtime.prepare(repo, state)
    assert dotenv_values(repo / ".env")["DEPLOYD_DB_PATH"] == str(custom / "db.sqlite3")


def test_custom_relative_path_is_rejected(runtime_paths):
    repo, state = runtime_paths
    (repo / ".env").write_text("DEPLOYD_DB_PATH=data/custom.sqlite3\n")
    with pytest.raises(ValueError, match="absolute path"):
        runtime.prepare(repo, state)


def test_symlinked_env_is_rejected(runtime_paths):
    repo, state = runtime_paths
    target = repo / "untouched"
    target.write_text("private")
    (repo / ".env").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        runtime.prepare(repo, state)
    assert target.read_text() == "private"


@pytest.fixture
def configured_runtime(runtime_paths, monkeypatch):
    repo, state = runtime_paths
    runtime.prepare(repo, state)
    (state / "apps.yaml").write_text("apps: {}\n")
    (state / "secrets.env").write_text("")
    monkeypatch.chdir(repo)
    for key, value in dotenv_values(repo / ".env").items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_app_registry.cache_clear()
    yield repo, state
    get_settings.cache_clear()
    get_app_registry.cache_clear()


def test_preflight_accepts_valid_runtime(configured_runtime):
    runtime.check()


def test_preflight_catches_missing_apps(configured_runtime):
    _, state = configured_runtime
    (state / "apps.yaml").unlink()
    with pytest.raises(ValueError, match="missing or invalid app configuration"):
        runtime.check()


def test_preflight_catches_unwritable_state(configured_runtime, monkeypatch):
    monkeypatch.setattr(runtime.os, "access", lambda path, mode: False)
    with pytest.raises(ValueError, match="cannot write runtime directory"):
        runtime.check()


def test_preflight_catches_unwritable_lock(configured_runtime, monkeypatch):
    _, state = configured_runtime
    lock = state / "deployd.sqlite3.lock"
    lock.touch()
    original_access = runtime.os.access
    monkeypatch.setattr(
        runtime.os, "access", lambda path, mode: path != lock and original_access(path, mode)
    )
    with pytest.raises(ValueError, match="cannot read/write runtime file"):
        runtime.check()


@pytest.mark.parametrize(
    "filename", ["deployd.sqlite3", "deployd.sqlite3.lock", "apps.yaml", "secrets.env"]
)
def test_preflight_rejects_symlinked_runtime_files(configured_runtime, filename):
    _, state = configured_runtime
    path = state / filename
    path.unlink(missing_ok=True)
    path.symlink_to(state / "missing-target")
    with pytest.raises(ValueError, match="symbolic link"):
        runtime.check()


def test_preflight_rejects_incompatible_proxy_port(configured_runtime, monkeypatch):
    monkeypatch.setenv("DEPLOYD_BIND_PORT", "8301")
    with pytest.raises(ValueError, match="proxy requires"):
        runtime.check()


def test_cli_checks_actual_dotenv_and_does_not_echo_token(runtime_paths):
    repo, state = runtime_paths
    token = runtime.prepare(repo, state)
    (state / "apps.yaml").write_text("apps: {}\n")
    (state / "secrets.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("DEPLOYD_")}
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "check", "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    assert not (state / "deployd.sqlite3").exists()


def test_env_backups_are_pruned_to_the_newest_three(runtime_paths):
    repo, state = runtime_paths
    for index in range(4):
        stale = repo / f".env.backup-old{index}"
        stale.write_text("stale")
        os.utime(stale, (1_000 + index, 1_000 + index))
    (repo / ".env").write_text("DEPLOYD_DB_PATH=deployd.sqlite3\n")
    runtime.prepare(repo, state)
    backups = sorted(repo.glob(".env.backup-*"), key=lambda path: path.stat().st_mtime_ns)
    assert len(backups) == 3
    assert [path.name for path in backups[:2]] == [".env.backup-old2", ".env.backup-old3"]
    assert backups[-1].read_text() == "DEPLOYD_DB_PATH=deployd.sqlite3\n"
