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
    (
        3,
        """
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);
CREATE TABLE user_apps (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app     TEXT NOT NULL,
    PRIMARY KEY (user_id, app)
) WITHOUT ROWID;
CREATE INDEX ix_user_apps_app ON user_apps(app);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX ix_sessions_user ON sessions(user_id);
CREATE INDEX ix_sessions_expires ON sessions(expires_at);
ALTER TABLE audit_log ADD COLUMN app TEXT;
CREATE INDEX ix_audit_app ON audit_log(app, id DESC);
-- History is listed newest first with rowid as the tie-break. Ascending indexes read
-- backwards give exactly that order, so no query has to sort.
DROP INDEX IF EXISTS ix_deploys_app_created;
CREATE INDEX ix_deploys_app_created ON deploys(app, created_at);
CREATE INDEX ix_deploys_created ON deploys(created_at);
-- "Is this app busy" is asked on every status refresh; it must not depend on history size.
CREATE INDEX ix_deploys_active ON deploys(app) WHERE status IN ('queued', 'running');
-- The audit log is always read by id; this index was never used.
DROP INDEX IF EXISTS ix_audit_at;
""",
    ),
]
SCHEMA_VERSION = _MIGRATIONS[-1][0]

MAX_STEP_OUTPUT_CHARS = 200_000
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
                    elif target == 3:
                        self._migrate_to_3(c, script)
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

    @staticmethod
    def _migrate_to_3(c: sqlite3.Connection, script: str) -> None:
        _run_statements(c, script)
        # Earlier rows named the app only in target; a cancelled deploy named the deploy.
        c.execute(
            "UPDATE audit_log SET app = target WHERE action != 'deploy.cancel' "
            "AND target IN (SELECT DISTINCT app FROM deploys)"
        )
        c.execute(
            "UPDATE audit_log SET app = (SELECT app FROM deploys WHERE deploy_id = target) "
            "WHERE action = 'deploy.cancel'"
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

    def maintain(self) -> None:
        """Hourly housekeeping: drop what expired, refresh planner statistics, trim the WAL."""
        self.purge_old_nonces()
        self.purge_expired_sessions()
        with self._conn() as c:
            c.execute("PRAGMA optimize")
        with self._conn() as c:
            # Outside a transaction; a busy database just skips this round.
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def quick_check(self) -> str:
        """'ok', or SQLite's description of what is damaged."""
        with self._conn() as c:
            return "; ".join(row[0] for row in c.execute("PRAGMA quick_check").fetchall())

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
        apps: frozenset[str] | None = None,
    ) -> list[dict]:
        """``apps`` limits the result to those applications; None means no limit."""
        clauses, params = [], []
        if apps is not None:
            if not apps:
                return []
            clauses.append(f"app IN ({', '.join('?' * len(apps))})")
            params.extend(sorted(apps))
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
        """The newest ``step`` among the app's recent deploys, read through indexes only."""
        with self._conn() as c:
            row = c.execute(
                "SELECT s.step, s.status, s.started_at, s.output, s.deploy_id "
                "FROM deploy_steps s WHERE s.step = ? AND s.deploy_id IN ("
                "  SELECT deploy_id FROM deploys WHERE app = ? "
                "  ORDER BY created_at DESC, rowid DESC LIMIT 50) "
                "ORDER BY s.id DESC LIMIT 1",
                (step, app),
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

    # --- audit ----------------------------------------------------------
    def audit(
        self,
        actor: str,
        action: str,
        target: str | None,
        detail: str | None,
        app: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_log (actor, action, target, detail, app) VALUES (?, ?, ?, ?, ?)",
                (actor, action, target, detail, app),
            )

    def list_audit(
        self, limit: int = 50, offset: int = 0, apps: frozenset[str] | None = None
    ) -> list[dict]:
        """``apps`` limits the result to entries about those applications."""
        where, params = "", []
        if apps is not None:
            if not apps:
                return []
            where = f"WHERE app IN ({', '.join('?' * len(apps))}) "
            params.extend(sorted(apps))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT id, at, actor, action, target, detail, app FROM audit_log {where}"
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- users ----------------------------------------------------------
    def create_user(self, username: str, password_hash: str, apps: list[str]) -> bool:
        """False when the username is taken."""
        with self._conn() as c:
            try:
                cur = c.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, password_hash),
                )
            except sqlite3.IntegrityError:
                return False
            c.executemany(
                "INSERT INTO user_apps (user_id, app) VALUES (?, ?)",
                [(cur.lastrowid, app) for app in sorted(set(apps))],
            )
            return True

    def list_users(self) -> list[dict]:
        with self._conn() as c:
            users = c.execute(
                "SELECT u.id, u.username, u.disabled, u.created_at, u.last_login_at, "
                "(SELECT count(*) FROM sessions s WHERE s.user_id = u.id "
                " AND s.expires_at > datetime('now')) AS sessions "
                "FROM users u ORDER BY u.username"
            ).fetchall()
            assigned: dict[int, list[str]] = {}
            for row in c.execute("SELECT user_id, app FROM user_apps ORDER BY app"):
                assigned.setdefault(row["user_id"], []).append(row["app"])
            return [
                {
                    "username": user["username"],
                    "disabled": bool(user["disabled"]),
                    "created_at": user["created_at"],
                    "last_login_at": user["last_login_at"],
                    "sessions": user["sessions"],
                    "apps": assigned.get(user["id"], []),
                }
                for user in users
            ]

    def get_user(self, username: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id, username, password_hash, disabled FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            return dict(row) if row else None

    def update_user(
        self,
        username: str,
        *,
        password_hash: str | None = None,
        disabled: bool | None = None,
        apps: list[str] | None = None,
        keep_session: str | None = None,
    ) -> bool:
        """Applies the given changes in one transaction; False when the user is unknown.

        A new password or a disabled account ends the user's sessions, except
        ``keep_session`` (the one that asked for its own password change).
        """
        with self._conn() as c:
            row = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                return False
            user_id = row["id"]
            if password_hash is not None:
                c.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
                )
            if disabled is not None:
                c.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if apps is not None:
                c.execute("DELETE FROM user_apps WHERE user_id = ?", (user_id,))
                c.executemany(
                    "INSERT INTO user_apps (user_id, app) VALUES (?, ?)",
                    [(user_id, app) for app in sorted(set(apps))],
                )
            if password_hash is not None or disabled:
                c.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                    (user_id, keep_session or ""),
                )
            return True

    def delete_user(self, username: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount == 1

    def forget_app(self, app: str) -> None:
        """A removed application must not grant access again if its name is reused."""
        with self._conn() as c:
            c.execute("DELETE FROM user_apps WHERE app = ?", (app,))

    # --- sessions -------------------------------------------------------
    def create_session(self, user_id: int, token_hash: str, ttl_seconds: int) -> str:
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) "
                "VALUES (?, ?, datetime('now', ?))",
                (token_hash, user_id, f"+{int(ttl_seconds)} seconds"),
            )
            c.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (user_id,))
            return c.execute(
                "SELECT expires_at FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()["expires_at"]

    def session_user(self, token_hash: str) -> dict | None:
        """The enabled user behind a live session, with the apps assigned to them."""
        with self._conn() as c:
            row = c.execute(
                "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > datetime('now') AND u.disabled = 0",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            apps = c.execute("SELECT app FROM user_apps WHERE user_id = ?", (row["id"],)).fetchall()
            return {"username": row["username"], "apps": frozenset(app["app"] for app in apps)}

    def delete_session(self, token_hash: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def purge_expired_sessions(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
