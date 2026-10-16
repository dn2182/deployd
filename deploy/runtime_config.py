"""Prepare and validate the Ubuntu service environment without discarding existing state."""

import argparse
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key


def prepare(repo: Path, state: Path) -> str:
    env = repo / ".env"
    if env.is_symlink():
        raise ValueError(".env must not be a symbolic link")
    values = dotenv_values(env, interpolate=False) if env.exists() else {}
    updates = {}
    paths = {
        "DEPLOYD_DB_PATH": ("deployd.sqlite3", state / "deployd.sqlite3"),
        "DEPLOYD_APPS_CONFIG": ("config/apps.yaml", state / "apps.yaml"),
        "DEPLOYD_SECRETS_FILE": ("config/secrets.env", state / "secrets.env"),
    }
    for key, (legacy, target) in paths.items():
        value = values.get(key) or legacy
        path = Path(value)
        if not path.is_absolute():
            source = repo / path
            if path != Path(legacy):
                raise ValueError(f"{key} must use an absolute path for the service")
            # Existing state must be migrated deliberately, including SQLite WAL files.
            candidates = [source]
            if key == "DEPLOYD_DB_PATH":
                candidates += [Path(f"{source}{suffix}") for suffix in ("-wal", "-shm", ".lock")]
            if any(candidate.exists() or candidate.is_symlink() for candidate in candidates):
                raise ValueError(
                    f"{key}: existing state at {source}; stop deployd and migrate it to "
                    f"{target}, then set the absolute path in .env before rerunning"
                )
            updates[key] = str(target)
    for key, default in {
        "DEPLOYD_BIND_HOST": "127.0.0.1",
        "DEPLOYD_BIND_PORT": "8300",
        "DEPLOYD_MAX_REQUEST_BYTES": "65536",
    }.items():
        if not values.get(key):
            updates[key] = default
    token = ""
    if not values.get("DEPLOYD_ADMIN_TOKEN"):
        token = secrets.token_hex(32)
        updates["DEPLOYD_ADMIN_TOKEN"] = token
    if not updates:
        return token

    fd, name = tempfile.mkstemp(prefix=".env.install-", dir=repo)
    os.close(fd)
    candidate = Path(name)
    try:
        if env.exists():
            shutil.copyfile(env, candidate)
            backup_fd, backup = tempfile.mkstemp(prefix=".env.backup-", dir=repo)
            with os.fdopen(backup_fd, "wb") as handle:
                handle.write(env.read_bytes())
            print(f"Existing environment backed up to {backup}", file=sys.stderr)
            _prune_backups(repo, keep=3)
        for key, value in updates.items():
            set_key(candidate, key, value, quote_mode="always")
        os.chmod(candidate, 0o600)
        os.replace(candidate, env)
    finally:
        candidate.unlink(missing_ok=True)
    return token


def _prune_backups(repo: Path, keep: int) -> None:
    backups = sorted(
        (path for path in repo.glob(".env.backup-*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    for path in backups[:-keep]:
        path.unlink()


def check() -> None:
    from deployd.config import get_app_registry, get_settings

    try:
        settings = get_settings()
    except ValueError:
        raise ValueError(
            "invalid .env settings; check paths, ports and admin token length"
        ) from None
    if settings.bind_host != "127.0.0.1" or settings.bind_port != 8300:
        raise ValueError("the Ubuntu proxy requires DEPLOYD_BIND_HOST=127.0.0.1 and port 8300")
    for path in (settings.db_path, settings.apps_config, settings.secrets_file):
        if not path.is_absolute():
            raise ValueError(f"service runtime path must be absolute: {path}")
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise ValueError(f"deployd cannot write runtime directory: {path.parent}")
    for path in (
        settings.db_path,
        Path(f"{settings.db_path}.lock"),
        Path(f"{settings.db_path}-wal"),
        Path(f"{settings.db_path}-shm"),
        settings.apps_config,
        settings.secrets_file,
    ):
        if path.is_symlink():
            raise ValueError(f"runtime file must not be a symbolic link: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"runtime path must be a regular file: {path}")
        if path.exists() and not os.access(path, os.R_OK | os.W_OK):
            raise ValueError(f"deployd cannot read/write runtime file: {path}")
    if not settings.secrets_file.is_file():
        raise ValueError(f"missing secrets file: {settings.secrets_file}")
    try:
        get_app_registry()
    except Exception:
        raise ValueError(f"missing or invalid app configuration: {settings.apps_config}") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "check"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/deployd"))
    args = parser.parse_args()
    os.chdir(args.repo)
    try:
        if args.mode == "prepare":
            print(prepare(args.repo, args.state), end="")
        else:
            check()
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Runtime configuration error: {exc}\n")


if __name__ == "__main__":
    main()
