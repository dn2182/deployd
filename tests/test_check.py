import os
import sqlite3

import pytest

from deployd import check, config
from deployd.store import db
from deployd.worker import directory_layout


@pytest.fixture
def env(tmp_path, monkeypatch):
    apps_yaml = tmp_path / "apps.yaml"
    apps_yaml.write_text(
        f"""
apps:
  app-x:
    releases_dir: {tmp_path}/releases
    current_link: {tmp_path}/current
    artifact:
      allowed_url_prefix: "https://example.com/"
    restart:
      command: ["true"]
    health:
      url: "http://127.0.0.1:1/healthz"
"""
    )
    secrets = tmp_path / "secrets.env"
    secrets.write_text("DEPLOYD_SECRET_APP_X=" + "s" * 32 + "\n")
    secrets.chmod(0o600)
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(apps_yaml))
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(secrets))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", "a" * 32)
    monkeypatch.delenv("DEPLOYD_SECRET_APP_X", raising=False)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


def test_clean_configuration_passes(env, capsys):
    assert check.main([]) == 0
    out = capsys.readouterr().out
    assert "0 error(s)" in out
    assert "no GitHub token" not in out
    assert not (env / "state.sqlite3").exists()
    assert not (env / "releases").exists()


def test_check_does_not_migrate_existing_database(env, monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(db, "_MIGRATIONS", db._MIGRATIONS[:1])
        db.Store(env / "state.sqlite3").init()
    before = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}
    assert check.run_checks().errors == []
    after = {p.name: p.read_bytes() for p in env.iterdir() if p.is_file()}
    assert after == before
    with sqlite3.connect(env / "state.sqlite3") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_check_reports_archive_recovery_without_renaming(env, monkeypatch):
    root = env / "releases"
    root.mkdir()
    old_name = f"{'a' * 40}-{'b' * 32}"
    new_name = f"{'c' * 40}-{'d' * 32}"
    archive = root / old_name
    archive.mkdir()
    directory_layout.write_manifest(archive, {"name": new_name, "previous": None, "created_at": 1})
    original = config.get_app_registry()["app-x"]
    spec = original.model_copy(
        update={"release_layout": "directory", "current_link": root / "current"}
    )
    monkeypatch.setattr(check, "get_app_registry", lambda: {"app-x": spec})
    lock = db.InstanceLock(env / "state.sqlite3")
    lock.acquire()
    try:
        before = set(env.rglob("*"))
        report = check.run_checks()
        assert any("needs recovery" in error for error in report.errors)
        assert set(env.rglob("*")) == before
        assert archive.is_dir() and not (root / new_name).exists()
    finally:
        lock.release()


def test_problems_are_reported(env, capsys, monkeypatch):
    (env / "secrets.env").write_text("DEPLOYD_SECRET_APP_X=short\n")
    (env / "secrets.env").chmod(0o644)
    (env / "apps.yaml").write_text(
        f"""
apps:
  app-x:
    releases_dir: {env}/releases
    current_link: {env}/current
    frozen: true
    artifact:
      allowed_url_prefix: "https://example.com/"
    github_repository: acme/site
    restart:
      command: ["/nonexistent/restart"]
    migrate:
      command: ["definitely-not-on-path-xyz"]
"""
    )
    (env / "apps.yaml").write_text(
        (env / "apps.yaml")
        .read_text()
        .replace(
            'allowed_url_prefix: "https://example.com/"',
            'allowed_url_prefix: "https://api.github.com/repos/acme/site/releases/assets/"',
        )
    )
    config.get_app_registry.cache_clear()
    monkeypatch.delenv("DEPLOYD_GITHUB_TOKEN", raising=False)
    assert check.main(["--quiet"]) == 1
    out = capsys.readouterr().out
    assert "signing secret is shorter than 32 bytes" in out
    assert "restart executable not found" in out
    assert "migrate executable not found" in out
    if os.name != "nt":
        assert "readable by others" in out
    assert "no GitHub token" in out
    assert "no health URL" in out
    assert "frozen" in out


def test_broken_apps_config_is_an_error(env, capsys):
    (env / "apps.yaml").write_text("apps: []\n")
    config.get_app_registry.cache_clear()
    assert check.main([]) == 1
    assert "apps config" in capsys.readouterr().out


def test_cli_dispatch(monkeypatch, env):
    from deployd import main as entry

    monkeypatch.setattr("sys.argv", ["deployd", "check", "--quiet"])
    with pytest.raises(SystemExit) as stop:
        entry.run()
    assert stop.value.code == 0
    monkeypatch.setattr("sys.argv", ["deployd", "bogus"])
    with pytest.raises(SystemExit) as stop:
        entry.run()
    assert stop.value.code == 2
