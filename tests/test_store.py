from concurrent.futures import ThreadPoolExecutor

import pytest

from deployd.store.db import InstanceLock, Store


def _store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    s.init()
    return s


def test_deploy_lifecycle(tmp_path):
    s = _store(tmp_path)
    did = s.create_deploy("app-a", "a" * 40, "https://x/artifact.zip", "b" * 64, "test")
    s.set_status(did, "running")
    s.add_step(did, "download", "succeeded")
    s.set_status(did, "succeeded", finished=True)

    row = s.get_deploy(did)
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None
    assert [st["step"] for st in row["steps"]] == ["download"]


def test_unknown_deploy_returns_none(tmp_path):
    assert _store(tmp_path).get_deploy("nope") is None


def test_nonce_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.consume_nonce("n1") is True
    assert s.consume_nonce("n1") is False


def test_nonce_consumption_is_atomic(tmp_path):
    s = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: s.consume_nonce("same"), range(32)))
    assert results.count(True) == 1
    assert results.count(False) == 31


def test_identical_deploy_request_is_idempotent(tmp_path):
    s = _store(tmp_path)
    args = ("n1", "request-hash", "app-a", "a" * 40, "https://x/a.zip", "b" * 64, "test")
    first = s.create_deploy_once(*args)
    second = s.create_deploy_once(*args)
    assert first[1] is True
    assert second == (first[0], False)
    assert s.create_deploy_once("n1", "different", *args[2:]) is None


def test_recovery_fails_running_and_returns_queued(tmp_path):
    s = _store(tmp_path)
    running = s.create_deploy("app-a", "a" * 40, "https://x/a.zip", "b" * 64, "test")
    queued = s.create_deploy("app-b", "c" * 40, "https://x/b.zip", "d" * 64, "test")
    s.set_status(running, "running")

    assert s.recover_after_restart() == [("app-b", queued, "artifact")]
    interrupted = s.get_deploy(running)
    assert interrupted["status"] == "failed"
    assert interrupted["steps"][-1]["step"] == "recovery"


def test_unversioned_database_is_upgraded_in_place(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE deploys (deploy_id TEXT PRIMARY KEY, app TEXT NOT NULL,
                commit_sha TEXT NOT NULL, artifact_url TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL, triggered_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT);
            CREATE TABLE deploy_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
                deploy_id TEXT NOT NULL REFERENCES deploys(deploy_id), step TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT (datetime('now')),
                output TEXT);
            CREATE TABLE nonces (nonce TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO deploys VALUES ('d1', 'app', 'a', 'local-release://previous', 'b',
                'activate:previous', 'succeeded', '2026-01-01 00:00:00', '2026-01-01 00:00:01');
            INSERT INTO deploys VALUES ('d2', 'app', 'a', 'https://x/a.zip', 'b',
                'ci', 'succeeded', '2026-01-01 00:00:00', '2026-01-01 00:00:01');
            """
        )
    s = Store(path)
    s.init()
    s.init()
    assert s.get_deploy("d1")["kind"] == "activate"
    assert s.get_deploy("d2")["kind"] == "artifact"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert {row[1] for row in conn.execute("PRAGMA table_info(nonces)")} >= {
            "request_hash",
            "deploy_id",
        }


def test_newer_schema_is_refused(tmp_path):
    import sqlite3

    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        Store(path).init()


def test_supersede_marks_only_older_queued_artifacts(tmp_path):
    s = _store(tmp_path)
    older = s.create_deploy("app", "a" * 40, "https://x/a.zip", "b" * 64, "ci")
    running = s.create_deploy("app", "a" * 40, "https://x/a.zip", "b" * 64, "ci")
    s.set_status(running, "running")
    other_app = s.create_deploy("other", "a" * 40, "https://x/a.zip", "b" * 64, "ci")
    activation = s.create_deploy("app", "a" * 40, "local-release://x", "b" * 64, "x", "activate")
    newest = s.create_deploy("app", "c" * 40, "https://x/c.zip", "d" * 64, "ci")

    assert s.supersede_queued("app", newest) == [older]
    assert s.get_deploy(older)["status"] == "superseded"
    assert s.get_deploy(older)["steps"][-1]["output"] == f"superseded by deploy {newest}"
    assert s.get_deploy(running)["status"] == "running"
    assert s.get_deploy(other_app)["status"] == "queued"
    assert s.get_deploy(activation)["status"] == "queued"
    assert s.get_deploy(newest)["status"] == "queued"


def test_cancel_only_applies_to_queued(tmp_path):
    s = _store(tmp_path)
    did = s.create_deploy("app", "a" * 40, "https://x/a.zip", "b" * 64, "ci")
    assert s.cancel_deploy(did, "ops") is True
    assert s.cancel_deploy(did, "ops") is False
    row = s.get_deploy(did)
    assert row["status"] == "cancelled"
    assert row["finished_at"] is not None
    assert row["steps"][-1]["output"] == "cancelled by ops"


def test_list_deploys_filters_and_pages(tmp_path):
    s = _store(tmp_path)
    ids = [s.create_deploy("app", "a" * 40, "https://x/a.zip", "b" * 64, "ci") for _ in range(3)]
    s.set_status(ids[0], "failed", finished=True)
    assert [row["deploy_id"] for row in s.list_deploys(limit=1, offset=1)] == [ids[1]]
    assert [row["deploy_id"] for row in s.list_deploys(status="failed")] == [ids[0]]
    assert s.list_deploys(app="nope") == []


def test_status_helpers(tmp_path):
    s = _store(tmp_path)
    first = s.create_deploy("app", "a" * 40, "https://x/a.zip", "b" * 64, "ci")
    s.set_status(first, "succeeded", finished=True)
    s.add_step(first, "health", "succeeded", output="healthy after 1 attempt(s)")
    second = s.create_deploy("app", "c" * 40, "https://x/c.zip", "d" * 64, "ci")
    assert s.count_queued("app") == 1
    assert s.last_deploy("app")["deploy_id"] == second
    assert s.last_step("app", "health")["output"] == "healthy after 1 attempt(s)"
    assert s.previous_succeeded_sha("app", second) == "a" * 40
    assert s.previous_succeeded_sha("app", first) is None
    assert s.get_status("missing") is None


def test_audit_log_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.audit("dan", "secret.rotate", "app", None)
    s.audit("dan", "app.freeze", "app", "x")
    rows = s.list_audit(limit=1)
    assert rows[0]["action"] == "app.freeze"
    assert s.list_audit(limit=5, offset=1)[0]["action"] == "secret.rotate"
    s.check_writable()


def test_instance_lock_rejects_second_owner(tmp_path):
    first = InstanceLock(tmp_path / "state.sqlite3")
    second = InstanceLock(tmp_path / "state.sqlite3")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another deployd process"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_schema_migration_is_atomic(tmp_path, monkeypatch):
    import sqlite3

    from deployd.store import db as db_module

    path = tmp_path / "v1.sqlite3"
    all_migrations = db_module._MIGRATIONS
    monkeypatch.setattr(db_module, "_MIGRATIONS", all_migrations[:1])
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 1)
    Store(path).init()
    monkeypatch.setattr(db_module, "_MIGRATIONS", all_migrations)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 2)
    original = Store._migrate_to_2

    def explode(c, script):
        original(c, script)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Store, "_migrate_to_2", staticmethod(explode))
    with pytest.raises(sqlite3.OperationalError):
        Store(path).init()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "kind" not in {row[1] for row in conn.execute("PRAGMA table_info(deploys)")}
    monkeypatch.setattr(Store, "_migrate_to_2", staticmethod(original))
    Store(path).init()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "kind" in {row[1] for row in conn.execute("PRAGMA table_info(deploys)")}
