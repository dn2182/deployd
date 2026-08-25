import sqlite3

import pytest

from deployd.migrate import MigrationError, Runner, split_batches


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


def test_go_batch_splitting():
    sql = "CREATE TABLE a (id INT)\nGO\nCREATE TABLE b (id INT)\ngo;\nSELECT 'GO inside string'"
    assert split_batches(sql) == [
        "CREATE TABLE a (id INT)",
        "CREATE TABLE b (id INT)",
        "SELECT 'GO inside string'",
    ]
