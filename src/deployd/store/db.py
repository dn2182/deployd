"""Only module that touches the database; parameterized queries only."""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

# Schema history is versioned with PRAGMA user_version. Version 0 databases
# predate versioning and may already hold the version 1 tables, so the first
# script only uses idempotent statements.
_MIGRATIONS = [
    (
        1,
        """
CREATE TABLE IF NOT EXISTS deploys (
    deploy_id    TEXT PRIMARY KEY,
    app          TEXT NOT NULL,
    commit_sha   TEXT NOT NULL,
    artifact_url TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_deploys_app_created ON deploys(app, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_deploys_status_created ON deploys(status, created_at);

CREATE TABLE IF NOT EXISTS deploy_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deploy_id  TEXT NOT NULL REFERENCES deploys(deploy_id),
    step       TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    output     TEXT
);

CREATE TABLE IF NOT EXISTS nonces (
    nonce        TEXT PRIMARY KEY,
    seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    request_hash TEXT,
    deploy_id    TEXT
);
""",
    ),
    (
        2,
        """
ALTER TABLE deploys ADD COLUMN kind TEXT NOT NULL DEFAULT 'artifact';
CREATE INDEX IF NOT EXISTS ix_deploy_steps_deploy ON deploy_steps(deploy_id, id);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL DEFAULT (datetime('now')),
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log(at DESC);
""",
    ),
]
SCHEMA_VERSION = _MIGRATIONS[-1][0]

MAX_STEP_OUTPUT_CHARS = 200_000
TERMINAL_STATUSES = ("succeeded", "failed", "rolled_back", "superseded", "cancelled")
KIND_PREFIXES = {"activate:": "activate", "connect:": "connect", "remove:": "remove"}


def _run_statements(c: sqlite3.Connection, script: str) -> None:
    """executescript would commit first; this keeps DDL inside the open transaction."""
    buffer = ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                c.execute(statement)
            buffer = ""
    if buffer.strip():
        c.execute(buffer)


class InstanceLock:
    def __init__(self, db_path: Path):
        self._path = db_path.with_name(db_path.name + ".lock")
        self._handle = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"another deployd process already owns state database {self._path.name}"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class Store:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    def init(self) -> None:
        with self._conn() as c:
            c.execute("PRAGMA journal_mode = WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"state database schema {version} is newer than this deployd ({SCHEMA_VERSION})"
                )
            for target, script in _MIGRATIONS:
                if version >= target:
                    continue
                # One transaction per version so a crash never leaves a half-applied step.
                c.execute("BEGIN")
                try:
                    if target == 1:
                        self._migrate_unversioned(c, script)
                    elif target == 2:
                        self._migrate_to_2(c, script)
                    else:
                        _run_statements(c, script)
                    c.execute(f"PRAGMA user_version = {target}")
                    c.execute("COMMIT")
                except BaseException:
                    c.execute("ROLLBACK")
                    raise

    @staticmethod
    def _migrate_unversioned(c: sqlite3.Connection, script: str) -> None:
        _run_statements(c, script)
        nonce_columns = {row["name"] for row in c.execute("PRAGMA table_info(nonces)").fetchall()}
        if "request_hash" not in nonce_columns:
            c.execute("ALTER TABLE nonces ADD COLUMN request_hash TEXT")
        if "deploy_id" not in nonce_columns:
            c.execute("ALTER TABLE nonces ADD COLUMN deploy_id TEXT")

    @staticmethod
    def _migrate_to_2(c: sqlite3.Connection, script: str) -> None:
        _run_statements(c, script)
        # Rows written before the kind column existed carried the kind in triggered_by.
        for prefix, kind in KIND_PREFIXES.items():
            c.execute(
                "UPDATE deploys SET kind = ? WHERE triggered_by LIKE ? AND kind = 'artifact'",
                (kind, prefix + "%"),
            )

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def check_writable(self) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("ROLLBACK")

    # --- nonces ---------------------------------------------------------
    def consume_nonce(self, nonce: str) -> bool:
        with self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO nonces (nonce) VALUES (?)", (nonce,))
            return cur.rowcount == 1

    def purge_old_nonces(self, older_than_hours: int = 24) -> None:
        with self._conn() as c:
            c.execute(
                "DELETE FROM nonces WHERE seen_at < datetime('now', ?)",
                (f"-{older_than_hours} hours",),
            )

    # --- deploys --------------------------------------------------------
    def create_deploy(
        self,
        app: str,
        commit_sha: str,
        artifact_url: str,
        artifact_sha256: str,
        triggered_by: str,
        kind: str = "artifact",
    ) -> str:
        deploy_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                """INSERT INTO deploys
                   (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by, kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by, kind),
            )
        return deploy_id

    def create_deploy_once(
        self,
        nonce: str,
        request_hash: str,
        app: str,
        commit_sha: str,
        artifact_url: str,
        artifact_sha256: str,
        triggered_by: str,
    ) -> tuple[str, bool] | None:
        """Atomically create once, or return the prior ID for an identical retry."""
        with self._conn() as c:
            claimed = c.execute(
                "INSERT OR IGNORE INTO nonces (nonce, request_hash) VALUES (?, ?)",
                (nonce, request_hash),
            )
            if claimed.rowcount == 0:
                prior = c.execute(
                    "SELECT request_hash, deploy_id FROM nonces WHERE nonce = ?", (nonce,)
                ).fetchone()
                if (
                    prior is not None
                    and prior["request_hash"] == request_hash
                    and prior["deploy_id"] is not None
                ):
                    return prior["deploy_id"], False
                return None

            deploy_id = uuid.uuid4().hex
            c.execute(
                """INSERT INTO deploys
                   (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by, kind)
                   VALUES (?, ?, ?, ?, ?, ?, 'artifact')""",
                (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by),
            )
            c.execute("UPDATE nonces SET deploy_id = ? WHERE nonce = ?", (deploy_id, nonce))
            return deploy_id, True

    def supersede_queued(self, app: str, newest_deploy_id: str) -> list[str]:
        """Older queued artifact deploys are pointless once a newer one is queued."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT deploy_id FROM deploys WHERE app = ? AND kind = 'artifact' "
                "AND status = 'queued' AND deploy_id != ? AND rowid < "
                "(SELECT rowid FROM deploys WHERE deploy_id = ?)",
                (app, newest_deploy_id, newest_deploy_id),
            ).fetchall()
            ids = [row["deploy_id"] for row in rows]
            for deploy_id in ids:
                c.execute(
                    "UPDATE deploys SET status = 'superseded', finished_at = datetime('now') "
                    "WHERE deploy_id = ? AND status = 'queued'",
                    (deploy_id,),
                )
                c.execute(
                    "INSERT INTO deploy_steps (deploy_id, step, status, output) "
                    "VALUES (?, 'queue', 'skipped', ?)",
                    (deploy_id, f"superseded by deploy {newest_deploy_id}"),
                )
            return ids

    def cancel_deploy(self, deploy_id: str, actor: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE deploys SET status = 'cancelled', finished_at = datetime('now') "
                "WHERE deploy_id = ? AND status = 'queued'",
                (deploy_id,),
            )
            if cur.rowcount == 0:
                return False
            c.execute(
                "INSERT INTO deploy_steps (deploy_id, step, status, output) "
                "VALUES (?, 'queue', 'skipped', ?)",
                (deploy_id, f"cancelled by {actor}"),
            )
            return True

    def set_status(self, deploy_id: str, status: str, *, finished: bool = False) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE deploys SET status = ?, "
                "finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END "
                "WHERE deploy_id = ?",
                (status, finished, deploy_id),
            )

    def add_step(self, deploy_id: str, step: str, status: str, output: str = "") -> None:
        if len(output) > MAX_STEP_OUTPUT_CHARS:
            output = output[:MAX_STEP_OUTPUT_CHARS] + "\n[output truncated]"
        with self._conn() as c:
            cur = c.execute(
                """UPDATE deploy_steps SET status = ?, output = ?
                   WHERE id = (
                       SELECT id FROM deploy_steps
                       WHERE deploy_id = ? AND step = ? AND status = 'running'
                       ORDER BY id DESC LIMIT 1
                   )""",
                (status, output, deploy_id, step),
            )
            if cur.rowcount == 0:
                c.execute(
                    "INSERT INTO deploy_steps (deploy_id, step, status, output) VALUES (?, ?, ?, ?)",
                    (deploy_id, step, status, output),
                )

    def recover_after_restart(self) -> list[tuple[str, str, str]]:
        """Fail interrupted work and return durable queued deployments in order."""
        with self._conn() as c:
            running = c.execute(
                "SELECT deploy_id FROM deploys WHERE status = 'running' ORDER BY created_at, rowid"
            ).fetchall()
            for row in running:
                c.execute(
                    "INSERT INTO deploy_steps (deploy_id, step, status, output) "
                    "VALUES (?, 'recovery', 'failed', "
                    "'service restarted; inspect current link and health before redeploying')",
                    (row["deploy_id"],),
                )
                c.execute(
                    "UPDATE deploys SET status = 'failed', finished_at = datetime('now') "
                    "WHERE deploy_id = ?",
                    (row["deploy_id"],),
                )
            queued = c.execute(
                "SELECT app, deploy_id, kind FROM deploys WHERE status = 'queued' "
                "ORDER BY created_at, rowid"
            ).fetchall()
            return [(row["app"], row["deploy_id"], row["kind"]) for row in queued]

    def has_active_deploys(self, app: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM deploys WHERE app = ? AND status IN ('queued', 'running') LIMIT 1",
                (app,),
            ).fetchone()
            return row is not None

    def count_queued(self, app: str) -> int:
        with self._conn() as c:
            return c.execute(
                "SELECT count(*) FROM deploys WHERE app = ? AND status = 'queued'", (app,)
            ).fetchone()[0]

    def list_deploys(
        self,
        limit: int = 50,
        app: str | None = None,
        status: str | None = None,
        offset: int = 0,
    ) -> list[dict]:
        clauses, params = [], []
        if app:
            clauses.append("app = ?")
            params.append(app)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM deploys {where}"
                "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_deploy(self, deploy_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM deploys WHERE deploy_id = ?", (deploy_id,)).fetchone()
            if row is None:
                return None
            steps = c.execute(
                "SELECT step, status, started_at, output FROM deploy_steps "
                "WHERE deploy_id = ? ORDER BY id",
                (deploy_id,),
            ).fetchall()
            return {**dict(row), "steps": [dict(s) for s in steps]}

    def get_status(self, deploy_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM deploys WHERE deploy_id = ?", (deploy_id,)
            ).fetchone()
            return row["status"] if row else None

    def last_deploy(self, app: str, kind: str | None = None) -> dict | None:
        with self._conn() as c:
            if kind:
                row = c.execute(
                    "SELECT * FROM deploys WHERE app = ? AND kind = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (app, kind),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM deploys WHERE app = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (app,),
                ).fetchone()
            return dict(row) if row else None

    def last_step(self, app: str, step: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT s.step, s.status, s.started_at, s.output, s.deploy_id "
                "FROM deploy_steps s JOIN deploys d ON d.deploy_id = s.deploy_id "
                "WHERE d.app = ? AND s.step = ? ORDER BY s.id DESC LIMIT 1",
                (app, step),
            ).fetchone()
            return dict(row) if row else None

    def previous_succeeded_sha(self, app: str, before_deploy_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT commit_sha FROM deploys WHERE app = ? AND kind = 'artifact' "
                "AND status = 'succeeded' AND rowid < "
                "(SELECT rowid FROM deploys WHERE deploy_id = ?) "
                "ORDER BY rowid DESC LIMIT 1",
                (app, before_deploy_id),
            ).fetchone()
            return row["commit_sha"] if row else None

    def purge_history(self, keep_days: int) -> int:
        """Finished deploys older than the retention window leave with their steps."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT deploy_id FROM deploys WHERE status IN (?, ?, ?, ?, ?) "
                "AND coalesce(finished_at, created_at) < datetime('now', ?)",
                (*TERMINAL_STATUSES, f"-{keep_days} days"),
            ).fetchall()
            ids = [row["deploy_id"] for row in rows]
            for deploy_id in ids:
                c.execute("DELETE FROM deploy_steps WHERE deploy_id = ?", (deploy_id,))
                c.execute("UPDATE nonces SET deploy_id = NULL WHERE deploy_id = ?", (deploy_id,))
                c.execute("DELETE FROM deploys WHERE deploy_id = ?", (deploy_id,))
            return len(ids)

    # --- audit ----------------------------------------------------------
    def audit(self, actor: str, action: str, target: str | None, detail: str | None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_log (actor, action, target, detail) VALUES (?, ?, ?, ?)",
                (actor, action, target, detail),
            )

    def list_audit(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, at, actor, action, target, detail FROM audit_log "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
