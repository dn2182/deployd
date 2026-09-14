#!/usr/bin/python3 -I
"""Change an existing management user's password without exposing credentials in argv."""

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

AUTH_DIR = Path("/var/lib/deployd-auth")
PASSWORD_FILE = AUTH_DIR / "htpasswd"
ROOT_UID = 0
HTPASSWD = "/usr/bin/htpasswd"


def protected(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_mode & 0o022
        or info.st_nlink > 1
        and not directory
    ):
        raise ValueError("unsafe credential path")
    return info


def htpasswd(path, username, password, verify=False):
    return subprocess.run(
        [HTPASSWD, *(["-vi"] if verify else ["-iB", "-C", "10"]), "--", str(path), username],
        input=password.encode() + b"\n",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    ).returncode


def change(payload):
    username = payload["username"]
    current = payload["current_password"]
    new = payload["new_password"]
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", username):
        raise ValueError("invalid username")
    for password in (current, new):
        if not isinstance(password, str) or not password or len(password.encode()) > 4096:
            raise ValueError("invalid password")
        if any(ord(char) < 32 or ord(char) == 127 for char in password):
            raise ValueError("invalid password")
    # Bcrypt silently truncates passwords beyond 72 bytes.
    if len(new) < 12 or len(new.encode()) > 72 or current == new:
        raise ValueError(
            "new password must be different, at least 12 characters and at most 72 UTF-8 bytes"
        )
    for parent in reversed(AUTH_DIR.parents):
        protected(parent, directory=True)
    protected(AUTH_DIR, directory=True)
    with open(AUTH_DIR / "password.lock", "a+b") as lock:
        protected(AUTH_DIR / "password.lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = protected(PASSWORD_FILE)
        users = [line.split(":", 1)[0] for line in PASSWORD_FILE.read_text().splitlines()]
        if users.count(username) != 1:
            return 3
        result = htpasswd(PASSWORD_FILE, username, current, verify=True)
        if result == 3:
            return 3
        if result:
            raise RuntimeError("password verification unavailable")
        fd, name = tempfile.mkstemp(prefix=".password-", dir=AUTH_DIR)
        staged = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(PASSWORD_FILE.read_bytes())
            if htpasswd(staged, username, new):
                raise RuntimeError("password update failed")
            os.chown(staged, ROOT_UID, info.st_gid)
            os.chmod(staged, 0o640)
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(staged, PASSWORD_FILE)
            directory_fd = os.open(AUTH_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            staged.unlink(missing_ok=True)
    return 0


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1:
            return 1
        data = sys.stdin.buffer.read(16385)
        if len(data) > 16384:
            return 1
        return change(json.loads(data))
    except Exception:
        # sudo forwards stderr to the API caller; never include payloads or subprocess output.
        return 1


if __name__ == "__main__":
    sys.exit(main())
