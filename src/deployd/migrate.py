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

_GO_SEPARATOR = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)
_MIGRATION_NAME = re.compile(r"^\d+[_-].+\.sql$")

_DDL = {
    "mssql": f"""
        IF OBJECT_ID('{VERSION_TABLE}', 'U') IS NULL
        CREATE TABLE {VERSION_TABLE} (
            filename   NVARCHAR(255) NOT NULL PRIMARY KEY,
            checksum   CHAR(64)      NOT NULL,
            applied_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
        )""",
    "sqlite": f"""
        CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
            filename   TEXT NOT NULL PRIMARY KEY,
            checksum   TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
}


class MigrationError(Exception):
    pass


def split_batches(sql: str) -> list[str]:
    return [b.strip() for b in _GO_SEPARATOR.split(sql) if b.strip()]


def discover(directory: Path) -> list[tuple[str, str, str]]:
    """Returns [(filename, sql, sha256)] in lexicographic (= version) order."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    out = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and _MIGRATION_NAME.match(path.name):
            sql = path.read_text()
            out.append((path.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    return out


class Runner:
    def __init__(self, conn, dialect: str):
        if dialect not in _DDL:
            raise MigrationError(f"unsupported dialect: {dialect}")
        self._conn = conn
        self._dialect = dialect

    def ensure_version_table(self) -> None:
        cur = self._conn.cursor()
        cur.execute(_DDL[self._dialect])
        self._conn.commit()

    def applied(self) -> dict[str, str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT filename, checksum FROM {VERSION_TABLE}")
        return {row[0]: row[1] for row in cur.fetchall()}

    def run(self, directory: Path) -> list[str]:
        self.ensure_version_table()
        applied = self.applied()
        newly_applied = []
        for filename, sql, checksum in discover(directory):
            if filename in applied:
                if applied[filename] != checksum:
                    raise MigrationError(
                        f"{filename} was modified after being applied "
                        f"(checksum {applied[filename][:12]} -> {checksum[:12]}); "
                        "migrations are immutable — add a new file instead"
                    )
                continue
            self._apply_one(filename, sql, checksum)
            newly_applied.append(filename)
        return newly_applied

    def _apply_one(self, filename: str, sql: str, checksum: str) -> None:
        cur = self._conn.cursor()
        try:
            for batch in split_batches(sql):
                cur.execute(batch)
            cur.execute(
                f"INSERT INTO {VERSION_TABLE} (filename, checksum) VALUES (?, ?)",
                (filename, checksum),
            )
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            raise MigrationError(f"{filename} failed: {exc}") from exc


def _connect_mssql(dsn: str):
    try:
        import pyodbc
    except ImportError as exc:
        raise MigrationError("pyodbc not installed — pip install 'deployd[mssql]'") from exc
    return pyodbc.connect(dsn, autocommit=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deployd-migrate")
    parser.add_argument("--dir", default="migrations", type=Path)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("DEPLOYD_MIGRATE_DSN"),
        help="ODBC connection string (default: env DEPLOYD_MIGRATE_DSN)",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        print("no DSN: pass --dsn or set DEPLOYD_MIGRATE_DSN", file=sys.stderr)
        return 2
    try:
        conn = _connect_mssql(args.dsn)
        try:
            applied = Runner(conn, "mssql").run(args.dir)
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


if __name__ == "__main__":
    sys.exit(main())
