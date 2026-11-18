#!/usr/bin/python3 -I
"""Privileged reader and writer for one app's .env file. No imports from the deployd checkout."""

import errno
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import signal
import stat
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

# Root-owned list of directories that may hold editable .env files, written by the installer.
ENV_ROOTS_FILE = Path("/etc/deployd/env-roots")
STATE_ROOT = Path("/var/lib/deployd-env")
# deployd's own configuration and state are never editable through this helper.
FORBIDDEN = (
    Path("/opt/deployd"),
    Path("/etc/deployd"),
    Path("/var/lib/deployd"),
    Path("/var/lib/deployd-auth"),
    Path("/var/lib/deployd-connect"),
    Path("/var/lib/deployd-env"),
    Path("/srv/deployd"),
)
ROOT_UID = 0
MAX_BYTES = 262_144
MAX_DEPTH = 3
KEEP_BACKUPS = 10
APP = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
PART = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
# .env, .env.production, api.env: nothing else is ever opened, whatever the roots allow.
NAME = re.compile(r"(?:\.env(?:\.[A-Za-z0-9_-]{1,32})?|[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.env)\Z")
ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)\Z", re.DOTALL)
OPERATIONS = ("check", "read", "write", "restore")


class Conflict(ValueError):
    pass


def env_roots():
    try:
        metadata = ENV_ROOTS_FILE.lstat()
    except FileNotFoundError:
        raise ValueError(
            "no .env directories are allowed; rerun the installer and enable the .env editor"
        ) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_mode & 0o022
        or metadata.st_size > 16384
    ):
        raise ValueError(f"{ENV_ROOTS_FILE} must be a root-owned file that others cannot write")
    roots = []
    for line in ENV_ROOTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        root = Path(line)
        if not root.is_absolute() or root.parent == root or ".." in root.parts:
            raise ValueError(f"invalid directory in {ENV_ROOTS_FILE}: {line}")
        if root not in roots:
            roots.append(root)
    return roots


def locate(value):
    """Splits an allowed path into its root and the parts below it, or raises."""
    path = Path(value)
    roots = env_roots()
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not NAME.fullmatch(path.name)
        or any(path.is_relative_to(denied) for denied in FORBIDDEN)
    ):
        raise ValueError(
            "expected an absolute path to a file named .env, .env.<name> or <name>.env"
        )
    for root in roots:
        if path.is_relative_to(root):
            below = path.relative_to(root).parts
            if 1 <= len(below) <= MAX_DEPTH + 1 and all(PART.fullmatch(part) for part in below):
                return root, below
    allowed = ", ".join(str(root) for root in roots) or "none"
    raise ValueError(
        f"the file must sit at most {MAX_DEPTH} folders below an allowed directory ({allowed}); "
        f"add directories to {ENV_ROOTS_FILE} and rerun the installer"
    )


@contextmanager
def parent_directory(root, below):
    """Opens the file's directory one component at a time, so no symlink is ever followed."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        walked = Path("/")
        for part in (*root.parts[1:], *below[:-1]):
            try:
                following = os.open(part, flags, dir_fd=fd)
            except OSError:
                raise ValueError(f"{walked / part} is not a real directory") from None
            os.close(fd)
            fd = following
            walked /= part
            if walked == root:
                metadata = os.fstat(fd)
                if metadata.st_uid != ROOT_UID or metadata.st_mode & 0o022:
                    raise ValueError(f"{root} must be root-owned and not writable by others")
        yield fd
    finally:
        os.close(fd)


def read_file(directory, name):
    """Returns (bytes, stat) for a plain, singly linked file, or (None, None) when absent."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None, None
    except OSError:
        raise ValueError("the .env path is a symbolic link or cannot be opened") from None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("the .env path must be a regular file without hard links")
        if metadata.st_size > MAX_BYTES:
            raise ValueError(f"the file is larger than {MAX_BYTES} bytes")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(MAX_BYTES + 1), metadata
    finally:
        os.close(fd)


def account_name(lookup, identity):
    try:
        return lookup(identity)[0]
    except KeyError:
        return str(identity)


def describe(metadata):
    return {
        "exists": True,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "owner": account_name(pwd.getpwuid, metadata.st_uid),
        "group": account_name(grp.getgrgid, metadata.st_gid),
        "size": metadata.st_size,
        "modified": int(metadata.st_mtime),
    }


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the file is not UTF-8 text") from None
    if "\x00" in text:
        raise ValueError("the file contains binary data")
    return text


def parse(text):
    """Returns ({key: value}, [line numbers that are not assignments])."""
    values, problems = {}, []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            problems.append(index)
            continue
        key, value = match.group(1), match.group(2).strip()
        quote = value[:1]
        if quote in ("'", '"') and (len(value) == 1 or not value.endswith(quote)):
            # A quoted value may span lines; without a closing quote it stays on its own line.
            for end in range(index, len(lines)):
                if lines[end].rstrip().endswith(quote):
                    value = "\n".join([value, *lines[index : end + 1]])
                    index = end + 1
                    break
        values[key] = value
    return values, problems


def difference(before, after):
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(key for key in set(before) & set(after) if before[key] != after[key]),
    }


@contextmanager
def backups(app, path):
    """Root-only storage for earlier versions, locked while a write is in progress."""
    folder = STATE_ROOT / app / digest(str(path).encode())[:16]
    for directory in (STATE_ROOT, STATE_ROOT / app, folder):
        with suppress(FileExistsError):
            os.mkdir(directory, 0o700)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_mode & 0o077
        ):
            raise ValueError(f"{directory} must be a private root-owned directory")
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    except BlockingIOError:
        raise Conflict("another change to this file is in progress") from None
    finally:
        os.close(fd)


def stored(folder):
    return sorted(name for name in os.listdir(folder) if re.fullmatch(r"\d{20}\.env", name))


def keep(folder, data):
    name = f"{time.time_ns():020d}.env"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(folder)
    return name


def prune(folder):
    for name in stored(folder)[:-KEEP_BACKUPS]:
        os.unlink(name, dir_fd=folder)
    os.fsync(folder)


def replace(directory, name, data, metadata):
    """Atomic rewrite that keeps the owner, group and mode the file already had."""
    staged = f".{name}.deployd-{os.getpid()}"
    fd = os.open(
        staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode))
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(staged, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staged, dir_fd=directory)
        raise


def normalise(content):
    if not isinstance(content, str):
        raise ValueError("content must be text")
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValueError("content cannot contain binary data")
    if text and not text.endswith("\n"):
        text += "\n"
    data = text.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError(f"content is larger than {MAX_BYTES} bytes")
    problems = parse(text)[1]
    if problems:
        raise ValueError(f"line {problems[0]} is not KEY=value or a # comment")
    return data


def save(directory, name, folder, current, metadata, data):
    result = {"saved": current != data, "sha256": digest(data)}
    result.update(difference(parse(decode(current))[0], parse(decode(data))[0]))
    if result["saved"]:
        kept = keep(folder, current)
        try:
            replace(directory, name, data, metadata)
        except OSError as exc:
            # Nothing was replaced, so the copy just stored is not an earlier version.
            os.unlink(kept, dir_fd=folder)
            if exc.errno == errno.EROFS:
                raise ValueError(
                    "the folder is read-only for the deployd service; list it in "
                    f"{ENV_ROOTS_FILE} and rerun the installer"
                ) from None
            raise
        prune(folder)
    result["backups"] = len(stored(folder))
    return result


def run(operation, app, value, payload=None):
    if operation not in OPERATIONS or not APP.fullmatch(app):
        raise ValueError("unknown operation or app name")
    root, below = locate(value)
    name = below[-1]
    with parent_directory(root, below) as directory:
        current, metadata = read_file(directory, name)
        if operation == "check":
            if current is None:
                return {"exists": False}
            try:
                count = len(stored_for(app, value))
            except OSError:
                count = 0
            return {**describe(metadata), "backups": count}
        if current is None:
            raise ValueError("the file does not exist; create it on the server first")
        if operation == "read":
            return {**describe(metadata), "content": decode(current), "sha256": digest(current)}
        with backups(app, Path(value)) as folder:
            if operation == "restore":
                names = stored(folder)
                if not names:
                    raise ValueError("no earlier version of this file is stored")
                fd = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read(MAX_BYTES + 1)
            else:
                data = normalise((payload or {}).get("content"))
                expected = (payload or {}).get("expected_sha256")
                if expected and expected != digest(current):
                    raise Conflict(
                        "the file changed on the server after it was loaded; reload it first"
                    )
            return save(directory, name, folder, current, metadata, data)


def stored_for(app, value):
    folder = STATE_ROOT / app / digest(str(Path(value)).encode())[:16]
    return stored(folder) if folder.is_dir() else []


def deadline_expired(signum, frame):
    raise TimeoutError("the .env operation timed out")


def main():
    try:
        if sys.platform != "linux" or os.geteuid() != 0 or len(sys.argv) != 4:
            raise ValueError("run the installed helper through its restricted sudo rule on Linux")
        signal.signal(signal.SIGALRM, deadline_expired)
        signal.alarm(30)
        payload = None
        if sys.argv[1] == "write":
            raw = sys.stdin.buffer.read(4 * MAX_BYTES + 1)
            if len(raw) > 4 * MAX_BYTES:
                raise ValueError("request is too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("invalid request")
        print(json.dumps(run(*sys.argv[1:], payload)))
    except Conflict as exc:
        print(json.dumps({"error": str(exc), "code": "conflict"}))
        return 1
    except (OSError, ValueError, KeyError) as exc:
        # File contents never reach this message: only paths and fixed text do.
        print(json.dumps({"error": str(exc), "code": "invalid"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
