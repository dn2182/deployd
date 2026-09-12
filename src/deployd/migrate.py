"""Forward-only SQL migration runner (`deployd-migrate`).

Applied migrations are immutable: a checksum change on an already-applied
file aborts the run. Version bookkeeping is parameterized; migration files
themselves are executed verbatim, batch by batch (GO separators supported).
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

VERSION_TABLE = "deploy_migrations"
LOCK_NAME = "deployd_migrate"
# Constant advisory-lock key shared by every app on the same PostgreSQL database.
PG_LOCK_KEY = 0x6465706C6F7964
LOCK_TIMEOUT_SECONDS = 60

_GO_SEPARATOR = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)
_MIGRATION_NAME = re.compile(r"^(\d+)[_-].+\.sql$")

_DDL = {
    "mssql": f"""
        IF OBJECT_ID('{VERSION_TABLE}', 'U') IS NULL
        CREATE TABLE {VERSION_TABLE} (
            filename   NVARCHAR(255) NOT NULL PRIMARY KEY,
            checksum   CHAR(64)      NOT NULL,
            applied_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
        )""",
    "postgres": f"""
        CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
            filename   VARCHAR(255) NOT NULL PRIMARY KEY,
            checksum   CHAR(64)     NOT NULL,
            applied_at TIMESTAMPTZ  NOT NULL DEFAULT now()
        )""",
    "sqlite": f"""
        CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
            filename   TEXT NOT NULL PRIMARY KEY,
            checksum   TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
}
_PARAM = {"mssql": "?", "postgres": "%s", "sqlite": "?"}


class MigrationError(Exception):
    pass


def split_batches(sql: str) -> list[str]:
    return [b.strip() for b in _GO_SEPARATOR.split(sql) if b.strip()]


def discover(directory: Path) -> list[tuple[str, str, str]]:
    """Returns [(filename, sql, sha256)] ordered by numeric prefix."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    found: dict[int, Path] = {}
    for path in directory.iterdir():
        match = _MIGRATION_NAME.match(path.name)
        if not path.is_file() or not match:
            continue
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"duplicate migration version {match.group(1)}: "
                f"{found[version].name} and {path.name}"
            )
        found[version] = path
    out = []
    for version in sorted(found):
        path = found[version]
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError(f"{path.name} is not valid UTF-8") from exc
        out.append((path.name, sql, hashlib.sha256(raw).hexdigest()))
    return out


def _drain(cur) -> None:
    nextset = getattr(cur, "nextset", None)
    if nextset is None:
        return
    while nextset():
        pass


class Runner:
    def __init__(self, conn, dialect: str):
        if dialect not in _DDL:
            raise MigrationError(f"unsupported dialect: {dialect}")
        self._conn = conn
        self._dialect = dialect
        self._param = _PARAM[dialect]

    def _lock_session(self) -> None:
        # Serializes runners sharing one database so two apps never interleave bookkeeping.
        if self._dialect != "mssql":
            return
        cur = self._conn.cursor()
        cur.execute(
            "DECLARE @result INT; "
            "EXEC @result = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = ?; "
            "SELECT @result",
            (LOCK_NAME, LOCK_TIMEOUT_SECONDS * 1000),
        )
        result = cur.fetchone()[0]
        _drain(cur)
        self._conn.commit()
        if result < 0:
            raise MigrationError(f"could not acquire migration lock (sp_getapplock={result})")

    def _lock_transaction(self, cur) -> None:
        if self._dialect == "postgres":
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (PG_LOCK_KEY,))
            _drain(cur)

    def release(self) -> None:
        if self._dialect == "mssql":
            cur = self._conn.cursor()
            cur.execute(
                "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session'", (LOCK_NAME,)
            )
            _drain(cur)
            self._conn.commit()

    def ensure_version_table(self) -> None:
        self._lock_session()
        cur = self._conn.cursor()
        self._lock_transaction(cur)
        cur.execute(_DDL[self._dialect])
        _drain(cur)
        self._conn.commit()

    def applied(self) -> dict[str, str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT filename, checksum FROM {VERSION_TABLE}")
        return {row[0]: row[1] for row in cur.fetchall()}

    def plan(self, directory: Path) -> tuple[list[str], list[str]]:
        """Returns (pending filenames, applied filenames whose checksum changed)."""
        self.ensure_version_table()
        applied = self.applied()
        pending, changed = [], []
        for filename, _sql, checksum in discover(directory):
            if filename not in applied:
                pending.append(filename)
            elif applied[filename] != checksum:
                changed.append(filename)
        return pending, changed

    def run(self, directory: Path) -> list[str]:
        try:
            self.ensure_version_table()
            applied = self.applied()
            newly_applied = []
            for filename, sql, checksum in discover(directory):
                if filename in applied:
                    if applied[filename] != checksum:
                        raise MigrationError(
                            f"{filename} was modified after being applied "
                            f"(checksum {applied[filename][:12]} -> {checksum[:12]}); "
                            "migrations are immutable, add a new file instead"
                        )
                    continue
                self._apply_one(filename, sql, checksum)
                newly_applied.append(filename)
            return newly_applied
        finally:
            self.release()

    def _trancount(self, cur) -> int:
        cur.execute("SELECT @@TRANCOUNT")
        value = cur.fetchone()[0]
        _drain(cur)
        return value

    def _apply_one(self, filename: str, sql: str, checksum: str) -> None:
        cur = self._conn.cursor()
        try:
            self._lock_transaction(cur)
            baseline = self._trancount(cur) if self._dialect == "mssql" else 0
            for batch in split_batches(sql):
                if self._dialect == "mssql":
                    batch = "SET XACT_ABORT ON;\n" + batch
                cur.execute(batch)
                _drain(cur)
            if self._dialect == "mssql" and self._trancount(cur) != baseline:
                raise MigrationError(
                    f"{filename} changed @@TRANCOUNT; migrations must not manage their own "
                    "transactions (each file already runs in one)"
                )
            cur.execute(
                f"INSERT INTO {VERSION_TABLE} (filename, checksum) "
                f"VALUES ({self._param}, {self._param})",
                (filename, checksum),
            )
            _drain(cur)
            self._conn.commit()
        except MigrationError:
            self._conn.rollback()
            raise
        except Exception as exc:
            self._conn.rollback()
            raise MigrationError(f"{filename} failed: {exc}") from exc


def _connect(dialect: str, dsn: str):
    if dialect == "mssql":
        try:
            import pyodbc
        except ImportError as exc:
            raise MigrationError("pyodbc not installed; pip install 'deployd[mssql]'") from exc
        return pyodbc.connect(dsn, autocommit=False)
    if dialect == "postgres":
        try:
            import psycopg
        except ImportError as exc:
            raise MigrationError("psycopg not installed; pip install 'deployd[postgres]'") from exc
        return psycopg.connect(dsn, autocommit=False)
    import sqlite3

    return sqlite3.connect(dsn)


def _resolve_dsn(args) -> str:
    if args.dsn:
        return args.dsn
    if args.dsn_file:
        try:
            value = Path(args.dsn_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MigrationError(f"cannot read --dsn-file: {exc}") from exc
        if value:
            return value
        raise MigrationError(f"--dsn-file is empty: {args.dsn_file}")
    value = os.environ.get(args.dsn_env, "")
    if value:
        return value
    raise MigrationError(f"no DSN: pass --dsn, --dsn-file, or set {args.dsn_env}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deployd-migrate",
        description="Apply pending SQL migrations in numeric filename order.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("up", "status"),
        default="up",
        help="up (default) applies pending migrations; status lists applied and pending files",
    )
    parser.add_argument("--dir", default="migrations", type=Path)
    parser.add_argument(
        "--dialect", choices=sorted(_DDL), default="mssql", help="database dialect (default: mssql)"
    )
    parser.add_argument(
        "--dsn",
        help="connection string; visible to other users in ps, prefer --dsn-env or --dsn-file",
    )
    parser.add_argument(
        "--dsn-env",
        default="DEPLOYD_MIGRATE_DSN",
        metavar="VAR",
        help="environment variable holding the DSN (default: DEPLOYD_MIGRATE_DSN)",
    )
    parser.add_argument(
        "--dsn-file", metavar="PATH", help="file whose first line is the DSN (keep it mode 0600)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list pending migrations and verify checksums without applying anything",
    )
    args = parser.parse_args(argv)

    try:
        dsn = _resolve_dsn(args)
        conn = _connect(args.dialect, dsn)
        try:
            runner = Runner(conn, args.dialect)
            if args.command == "status" or args.dry_run:
                return _report(runner, args.dir, strict=args.dry_run)
            applied = runner.run(args.dir)
        finally:
            conn.close()
    except MigrationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    if applied:
        print(f"applied {len(applied)}: {', '.join(applied)}")
    else:
        print("up to date")
    return 0


def _report(runner: Runner, directory: Path, *, strict: bool) -> int:
    try:
        pending, changed = runner.plan(directory)
        applied = runner.applied()
    finally:
        runner.release()
    for filename in sorted(applied):
        print(f"applied  {filename}")
    for filename in pending:
        print(f"pending  {filename}")
    for filename in changed:
        print(f"CHANGED  {filename} (applied checksum differs from file)")
    if changed and strict:
        print(f"FAILED: {len(changed)} applied migration(s) modified", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
