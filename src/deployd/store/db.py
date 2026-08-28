"""Only module that touches the database; parameterized queries only."""

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

CREATE TABLE IF NOT EXISTS deploy_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deploy_id  TEXT NOT NULL REFERENCES deploys(deploy_id),
    step       TEXT NOT NULL,
    status     TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    output     TEXT
);

CREATE TABLE IF NOT EXISTS nonces (
    nonce   TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    def init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- nonces ---------------------------------------------------------
    def nonce_seen(self, nonce: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM nonces WHERE nonce = ?", (nonce,)).fetchone()
            return row is not None

    def record_nonce(self, nonce: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO nonces (nonce) VALUES (?)", (nonce,))

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

    def set_status(self, deploy_id: str, status: str, *, finished: bool = False) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE deploys SET status = ?, "
                "finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END "
                "WHERE deploy_id = ?",
                (status, finished, deploy_id),
            )

    def add_step(self, deploy_id: str, step: str, status: str, output: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO deploy_steps (deploy_id, step, status, output) VALUES (?, ?, ?, ?)",
                (deploy_id, step, status, output),
            )

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
