import hashlib
import sqlite3

import pytest

from deployd.migrate import MigrationError, Runner, discover, main, split_batches


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "app.sqlite3")
    yield c
    c.close()


@pytest.fixture
def migrations(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_users.sql").write_text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    (d / "002_orders.sql").write_text(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)"
    )
    return d


def test_applies_in_order_and_records(conn, migrations):
    applied = Runner(conn, "sqlite").run(migrations)
    assert applied == ["001_users.sql", "002_orders.sql"]
    rows = conn.execute("SELECT filename FROM deploy_migrations ORDER BY filename").fetchall()
    assert [r[0] for r in rows] == applied
    conn.execute("INSERT INTO users (name) VALUES ('dan')")


def test_second_run_is_noop(conn, migrations):
    Runner(conn, "sqlite").run(migrations)
    assert Runner(conn, "sqlite").run(migrations) == []


def test_modified_applied_file_aborts(conn, migrations):
    Runner(conn, "sqlite").run(migrations)
    (migrations / "001_users.sql").write_text("CREATE TABLE users2 (id INTEGER)")
    with pytest.raises(MigrationError, match="immutable"):
        Runner(conn, "sqlite").run(migrations)


def test_failure_rolls_back_and_halts(conn, migrations):
    (migrations / "003_bad.sql").write_text("CREATE BROKEN SYNTAX")
    (migrations / "004_after.sql").write_text("CREATE TABLE later (id INTEGER)")
    with pytest.raises(MigrationError, match="003_bad.sql"):
        Runner(conn, "sqlite").run(migrations)

    recorded = {r[0] for r in conn.execute("SELECT filename FROM deploy_migrations")}
    assert recorded == {"001_users.sql", "002_orders.sql"}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "later" not in tables


def test_non_migration_files_ignored(conn, migrations):
    (migrations / "README.md").write_text("not sql")
    (migrations / "helper.sql").write_text("SELECT 1")
    assert len(Runner(conn, "sqlite").run(migrations)) == 2


def test_numeric_order_beats_lexicographic_order(conn, migrations):
    (migrations / "10_late.sql").write_text("CREATE TABLE late (id INTEGER)")
    (migrations / "3_middle.sql").write_text("CREATE TABLE middle (id INTEGER)")
    assert [name for name, _, _ in discover(migrations)] == [
        "001_users.sql",
        "002_orders.sql",
        "3_middle.sql",
        "10_late.sql",
    ]


def test_duplicate_prefix_is_rejected(migrations):
    (migrations / "002_duplicate.sql").write_text("SELECT 1")
    with pytest.raises(MigrationError, match="duplicate migration version 002"):
        discover(migrations)


def test_checksum_covers_utf8_bytes(migrations):
    (migrations / "003_names.sql").write_bytes("INSERT INTO t VALUES ('José')".encode())
    (_, sql, checksum) = discover(migrations)[-1]
    assert "José" in sql
    assert checksum == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    (migrations / "004_bad.sql").write_bytes(b"SELECT '\xff'")
    with pytest.raises(MigrationError, match="UTF-8"):
        discover(migrations)


def test_go_batch_splitting():
    sql = "CREATE TABLE a (id INT)\nGO\nCREATE TABLE b (id INT)\ngo;\nSELECT 'GO inside string'"
    assert split_batches(sql) == [
        "CREATE TABLE a (id INT)",
        "CREATE TABLE b (id INT)",
        "SELECT 'GO inside string'",
    ]


class _MultiResultCursor:
    """Mimics pyodbc: an error in a later result set surfaces only through nextset()."""

    def __init__(self, inner, failing_batches):
        self._inner = inner
        self._failing = failing_batches
        self._pending_error = None

    def execute(self, sql, params=()):
        if any(marker in sql for marker in self._failing):
            self._pending_error = RuntimeError("second result set failed")
            return self
        return self._inner.execute(sql, params)

    def nextset(self):
        if self._pending_error is not None:
            error, self._pending_error = self._pending_error, None
            raise error
        return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _MultiResultConnection:
    def __init__(self, inner, failing_batches):
        self._inner = inner
        self._failing = failing_batches

    def cursor(self):
        return _MultiResultCursor(self._inner.cursor(), self._failing)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_result_sets_are_drained_so_late_errors_fail_the_migration(conn, migrations):
    (migrations / "003_multi.sql").write_text("SELECT 1; RAISERROR-LATER")
    wrapped = _MultiResultConnection(conn, {"RAISERROR-LATER"})
    with pytest.raises(MigrationError, match="003_multi.sql failed: second result set failed"):
        Runner(wrapped, "sqlite").run(migrations)
    recorded = {r[0] for r in conn.execute("SELECT filename FROM deploy_migrations")}
    assert "003_multi.sql" not in recorded


def test_cli_dsn_sources_and_dry_run(tmp_path, migrations, monkeypatch, capsys):
    db = tmp_path / "cli.sqlite3"
    monkeypatch.delenv("DEPLOYD_MIGRATE_DSN", raising=False)
    assert main(["--dialect", "sqlite", "--dir", str(migrations)]) == 1
    assert "no DSN" in capsys.readouterr().err

    dsn_file = tmp_path / "dsn"
    dsn_file.write_text(f"{db}\n")
    argv = ["--dialect", "sqlite", "--dir", str(migrations), "--dsn-file", str(dsn_file)]
    assert main([*argv, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pending  001_users.sql" in out and "applied" not in out
    tables = sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall()
    assert ("users",) not in tables

    monkeypatch.setenv("MY_DSN", str(db))
    assert main(["--dialect", "sqlite", "--dir", str(migrations), "--dsn-env", "MY_DSN"]) == 0
    assert "applied 2" in capsys.readouterr().out

    assert main([*argv, "status"]) == 0
    out = capsys.readouterr().out
    assert "applied  001_users.sql" in out and "pending" not in out

    (migrations / "001_users.sql").write_text("CREATE TABLE users (id INTEGER)")
    assert main([*argv, "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "CHANGED  001_users.sql" in captured.out
    assert "modified" in captured.err
    assert main([*argv, "status"]) == 0


def test_help_warns_about_dsn_visibility(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "visible to other users in ps" in capsys.readouterr().out
