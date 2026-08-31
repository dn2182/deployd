"""Only module that touches the database; parameterized queries only."""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
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
"""

MAX_STEP_OUTPUT_CHARS = 200_000


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
            c.executescript(_SCHEMA)
            nonce_columns = {
                row["name"] for row in c.execute("PRAGMA table_info(nonces)").fetchall()
            }
            if "request_hash" not in nonce_columns:
                c.execute("ALTER TABLE nonces ADD COLUMN request_hash TEXT")
            if "deploy_id" not in nonce_columns:
                c.execute("ALTER TABLE nonces ADD COLUMN deploy_id TEXT")

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
        self, app: str, commit_sha: str, artifact_url: str, artifact_sha256: str, triggered_by: str
    ) -> str:
        deploy_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                """INSERT INTO deploys
                   (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by),
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
                   (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (deploy_id, app, commit_sha, artifact_url, artifact_sha256, triggered_by),
            )
            c.execute("UPDATE nonces SET deploy_id = ? WHERE nonce = ?", (deploy_id, nonce))
            return deploy_id, True

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

    def recover_after_restart(self) -> list[tuple[str, str]]:
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
                "SELECT app, deploy_id FROM deploys WHERE status = 'queued' "
                "ORDER BY created_at, rowid"
            ).fetchall()
            return [(row["app"], row["deploy_id"]) for row in queued]

    def has_active_deploys(self, app: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM deploys WHERE app = ? AND status IN ('queued', 'running') LIMIT 1",
                (app,),
            ).fetchone()
            return row is not None

    def list_deploys(self, limit: int = 50, app: str | None = None) -> list[dict]:
        with self._conn() as c:
            if app:
                rows = c.execute(
                    "SELECT * FROM deploys WHERE app = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (app, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM deploys ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
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
