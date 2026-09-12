#!/usr/bin/python3 -I
"""Privileged, Linux-only website adoption. No imports from the deployd checkout."""

import ctypes
import fcntl
import json
import os
import pwd
import re
import shutil
import signal
import stat
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

WEB_ROOT = Path("/var/www")
RELEASE_ROOT = Path("/srv/deployd")
STATE_ROOT = Path("/var/lib/deployd-connect")
MANIFEST = ".deployd-release.json"
ROOT_UID = 0
APP = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
SITE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
VERSION = re.compile(r"(?:[a-f0-9]{40}-[a-f0-9]{32}|b4deployd)\Z")


@contextmanager
def directory(path, parent=None):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def open_path(stack, path):
    fd = stack.enter_context(directory("/"))
    for component in path.parts[1:]:
        fd = stack.enter_context(directory(component, fd))
    return fd


def info(fd, name):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def identity(value):
    return [value.st_dev, value.st_ino] if value else None


def read_json(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            raise ValueError("invalid connection metadata")
        return json.loads(os.read(handle, 4097))
    finally:
        os.close(handle)


def write_json(fd, name, data, owner=None):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        payload = json.dumps(data).encode()
        if os.write(handle, payload) != len(payload):
            raise OSError("incomplete metadata write")
        if owner:
            os.fchown(handle, *owner)
        os.fsync(handle)
    finally:
        os.close(handle)
    os.fsync(fd)


def rename(left_fd, left, right_fd, right, flags):
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "renameat2"):
        raise ValueError("atomic website connection requires Linux renameat2")
    operation = libc.renameat2
    operation.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    operation.restype = ctypes.c_int
    if operation(left_fd, os.fsencode(left), right_fd, os.fsencode(right), flags):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    os.fsync(left_fd)
    os.fsync(right_fd)


def mount_id(fd):
    if sys.platform != "linux":
        return os.fstat(fd).st_dev
    # Bind mounts can share st_dev while exposing directories outside the approved tree.
    for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines():
        if line.startswith("mnt_id:"):
            return int(line.split()[1])
    raise ValueError("cannot verify filesystem mount boundaries")


def scan(fd, device, *, owner=None, manifest=False, budget=None, depth=0, mount=None):
    """Descriptor-relative traversal never follows links or alters regular-file ownership."""
    if budget is None:
        budget = [0, 0]
    if mount is None:
        mount = mount_id(fd)
    if depth > 64 or os.fstat(fd).st_dev != device or mount_id(fd) != mount:
        raise ValueError("website contains a mount or excessive directory nesting")
    for name in os.listdir(fd):
        value = os.stat(name, dir_fd=fd, follow_symlinks=False)
        budget[0] += 1
        budget[1] += value.st_size
        if budget[0] > 100000 or budget[1] > 2 * 1024**3:
            raise ValueError("automatic connection supports at most 100,000 entries and 2 GiB")
        if name == MANIFEST and depth == 0:
            if manifest and stat.S_ISREG(value.st_mode) and value.st_nlink == 1:
                continue
            raise ValueError("original website contains reserved release metadata")
        if value.st_dev != device:
            raise ValueError("website contains a mount")
        if stat.S_ISDIR(value.st_mode):
            with directory(name, fd) as child:
                scan(child, device, owner=owner, budget=budget, depth=depth + 1, mount=mount)
        elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ValueError(
                "website must contain only directories and regular, non-hardlinked files"
            )
        elif not value.st_mode & stat.S_IROTH:
            raise ValueError("website files must already be publicly readable (normally mode 0644)")
    if owner:
        os.fchown(fd, *owner)
        os.fchmod(fd, 0o755)
    elif not os.fstat(fd).st_mode & stat.S_IXOTH:
        raise ValueError("website directories must be accessible to Nginx (normally mode 0755)")


def expected_link(fd, name, target):
    value = info(fd, name)
    return bool(value and stat.S_ISLNK(value.st_mode) and os.readlink(name, dir_fd=fd) == target)


def copy_site(source, destination, owner, *, budget=None, mount=None, depth=0):
    if budget is None:
        budget = [0, 0]
        mount = mount_id(source)
    if depth > 64 or mount_id(source) != mount:
        raise ValueError("live website contains a mount or excessive nesting")
    for name in os.listdir(source):
        if name == MANIFEST and depth == 0:
            continue
        value = os.stat(name, dir_fd=source, follow_symlinks=False)
        budget[0] += 1
        budget[1] += value.st_size
        if budget[0] > 100000 or budget[1] > 2 * 1024**3:
            raise ValueError("website exceeds the restore copy limit")
        if stat.S_ISDIR(value.st_mode):
            os.mkdir(name, mode=0o700, dir_fd=destination)
            with directory(name, source) as child, directory(name, destination) as copied:
                copy_site(child, copied, owner, budget=budget, mount=mount, depth=depth + 1)
        elif stat.S_ISREG(value.st_mode):
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
            try:
                before = os.fstat(handle)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or identity(before) != identity(value)
                    or before.st_nlink != 1
                    or before.st_size != value.st_size
                    or mount_id(handle) != mount
                ):
                    raise ValueError(
                        "live files changed while restoring; retry with writers stopped"
                    )
                output = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=destination,
                )
                try:
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(handle, min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("live file changed during copy")
                        remaining -= len(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(output, view)
                            if written == 0:
                                raise OSError("incomplete website copy")
                            view = view[written:]
                    after = os.fstat(handle)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ):
                        raise ValueError("live file changed during copy")
                    os.fchown(output, *owner)
                    os.fchmod(output, before.st_mode & 0o777)
                    os.fsync(output)
                finally:
                    os.close(output)
            finally:
                os.close(handle)
        else:
            raise ValueError("live site contains a link or special file")
    os.fchown(destination, *owner)
    os.fchmod(destination, 0o755)
    os.fsync(destination)


def disconnect(app, site, owner):
    if not APP.fullmatch(app) or not SITE.fullmatch(site):
        raise ValueError("expected an app name and a direct /var/www child name")
    target = str(RELEASE_ROOT / app / "releases/current")
    with ExitStack() as stack:
        web = open_path(stack, WEB_ROOT)
        state = open_path(stack, STATE_ROOT)
        for fd, mask in ((web, 0o022), (state, 0o077)):
            value = os.fstat(fd)
            if value.st_uid != ROOT_UID or value.st_mode & mask:
                raise ValueError("website parent and helper state must be root-owned and protected")
        lock = os.open("lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=state)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if info(state, app) is None:
            os.mkdir(app, mode=0o700, dir_fd=state)
            os.fsync(state)
        transaction = stack.enter_context(directory(app, state))
        if info(transaction, "journal.json") and not info(transaction, "complete.json"):
            raise ValueError("finish the interrupted website connection before removing it")
        restore_name = f"restore-{site}"
        if info(transaction, restore_name) is None:
            os.mkdir(restore_name, mode=0o700, dir_fd=transaction)
            os.fsync(transaction)
        transaction = stack.enter_context(directory(restore_name, transaction))
        if info(transaction, "detach.json"):
            receipt = read_json(transaction, "detach.json")
            if receipt["site"] != site:
                raise ValueError("restore journal belongs to another website")
            if identity(info(web, site)) == receipt["copied"]:
                if expected_link(transaction, "detached-files", target):
                    os.unlink("detached-files", dir_fd=transaction)
                    os.fsync(transaction)
                elif info(transaction, "detached-files"):
                    raise ValueError(
                        "unexpected restore staging files; operator inspection required"
                    )
                return {"status": "restored", "path": str(WEB_ROOT / site)}
            if not expected_link(web, site, target):
                raise ValueError("website changed after restore; refusing to overwrite it")
            if identity(info(transaction, "detached-files")) != receipt["copied"]:
                raise ValueError("restore staging changed; operator inspection required")
            current = open_path(stack, Path(target))
            if read_json(current, MANIFEST).get("name") != receipt["version"]:
                raise ValueError(
                    "live version changed after interrupted restore; operator inspection required"
                )
        else:
            existing = info(web, site)
            if existing is None or stat.S_ISDIR(existing.st_mode):
                return {"status": "unlinked", "path": str(WEB_ROOT / site)}
            if not expected_link(web, site, target):
                raise ValueError("local path is not this app's symlink; refusing to replace it")
            current = open_path(stack, Path(target))
            if len({mount_id(web), mount_id(state), mount_id(current)}) != 1:
                raise ValueError("website and helper storage must share the current release mount")
            data = read_json(current, MANIFEST)
            if not isinstance(data, dict) or not VERSION.fullmatch(str(data.get("name", ""))):
                raise ValueError("current release metadata is invalid")
            scan(current, os.fstat(current).st_dev, manifest=True)
            # Only this private copy is disposable. Live releases and originals are never removed.
            if info(transaction, "detached-files"):
                shutil.rmtree("detached-files", dir_fd=transaction)
            os.mkdir("detached-files", mode=0o700, dir_fd=transaction)
            with directory("detached-files", transaction) as copied:
                copy_site(current, copied, owner)
                scan(copied, os.fstat(copied).st_dev)
                index = info(copied, "index.html")
                if not index or not stat.S_ISREG(index.st_mode) or not index.st_size:
                    raise ValueError("restored website requires a non-empty index.html")
                if info(transaction, "detach.pending.json"):
                    os.unlink("detach.pending.json", dir_fd=transaction)
                write_json(
                    transaction,
                    "detach.pending.json",
                    {
                        "site": site,
                        "copied": identity(os.fstat(copied)),
                        "version": data["name"],
                    },
                )
            rename(transaction, "detach.pending.json", transaction, "detach.json", 1)
        if not expected_link(web, site, target):
            raise ValueError("website changed during restore; no live files were overwritten")
        rename(web, site, transaction, "detached-files", 2)
        if not expected_link(transaction, "detached-files", target):
            raise ValueError("unexpected displaced link; operator inspection required")
        os.unlink("detached-files", dir_fd=transaction)
        os.fsync(transaction)
        return {"status": "restored", "path": str(WEB_ROOT / site)}


def connect(operation, app, site, owner):
    if operation not in {"check", "connect"} or not APP.fullmatch(app) or not SITE.fullmatch(site):
        raise ValueError("expected check|connect, an app name, and a direct /var/www child name")
    target = str(RELEASE_ROOT / app / "releases/current")
    with ExitStack() as stack:
        web = open_path(stack, WEB_ROOT)
        state = open_path(stack, STATE_ROOT)
        for fd, mask in ((web, 0o022), (state, 0o077)):
            value = os.fstat(fd)
            if value.st_uid != ROOT_UID or value.st_mode & mask:
                raise ValueError("website parent and helper state must be root-owned and protected")
        lock = os.open("lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=state)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        releases = open_path(stack, RELEASE_ROOT / app / "releases")
        current = stack.enter_context(directory("current", releases))
        data = read_json(current, MANIFEST)
        if not isinstance(data, dict) or not VERSION.fullmatch(str(data.get("name", ""))):
            raise ValueError("deploy a managed version before connecting the website")
        index = info(current, "index.html")
        if not index or not stat.S_ISREG(index.st_mode) or not index.st_size:
            raise ValueError("current/index.html must be a non-empty regular file")
        for path in (RELEASE_ROOT, RELEASE_ROOT / app, RELEASE_ROOT / app / "releases"):
            fd = open_path(stack, path)
            if not os.fstat(fd).st_mode & stat.S_IXOTH:
                raise ValueError("release parent directories must be accessible to Nginx")
        scan(current, os.fstat(current).st_dev, manifest=True)
        journal_exists = info(state, app) is not None
        linked = expected_link(web, site, target)
        if not journal_exists and linked:
            return {"status": "connected", "backup": False}
        if not journal_exists:
            original = info(web, site)
            if not original or not stat.S_ISDIR(original.st_mode):
                raise ValueError(
                    "local site must be an existing real directory, not a different symlink"
                )
            if info(releases, "b4deployd"):
                raise ValueError("b4deployd already exists; refusing to overwrite it")
            if (
                len(
                    {
                        original.st_dev,
                        os.fstat(web).st_dev,
                        os.fstat(state).st_dev,
                        os.fstat(releases).st_dev,
                    }
                )
                != 1
            ):
                raise ValueError(
                    "website, releases and helper state must be on the same filesystem"
                )
            with directory(site, web) as original_fd:
                if (
                    len({mount_id(original_fd), mount_id(web), mount_id(state), mount_id(releases)})
                    != 1
                ):
                    raise ValueError("website, releases and helper state must share one mount")
                scan(original_fd, original.st_dev)
            if operation == "check":
                return {"status": "ready", "backup": False}
            os.mkdir(app, mode=0o700, dir_fd=state)
            os.fsync(state)
        transaction = stack.enter_context(directory(app, state))
        if info(transaction, "journal.json") is None:
            # A crash before journaling leaves the untouched website in place.
            if linked or info(transaction, "original") or info(releases, "b4deployd"):
                raise ValueError("incomplete connection journal; operator inspection required")
            original = info(web, site)
            if not original or not stat.S_ISDIR(original.st_mode):
                raise ValueError("original website is missing")
            if operation == "check":
                return {"status": "ready", "backup": False}
            with directory(site, web) as original_fd:
                scan(original_fd, original.st_dev)
            write_json(
                transaction,
                "journal.json",
                {
                    "site": site,
                    "original": identity(original),
                    "created_at": time.time(),
                },
            )
        journal = read_json(transaction, "journal.json")
        if journal["site"] != site:
            raise ValueError("this app already has a connection journal for a different website")
        if info(transaction, "complete.json"):
            if not linked:
                raise ValueError(
                    "previously connected website was changed manually; inspect before reconnecting"
                )
            return {"status": "connected", "backup": True}
        original_id = journal["original"]
        staged = info(transaction, "original")
        archived = info(releases, "b4deployd")
        if linked and not staged and identity(archived) == original_id:
            if operation == "check":
                return {"status": "recovery", "backup": True}
            write_json(transaction, "complete.json", {"connected": True})
            return {"status": "connected", "backup": True}
        if operation == "check":
            return {"status": "recovery", "backup": False}
        if not linked:
            if identity(info(web, site)) != original_id or archived:
                raise ValueError("original website changed; refusing connection")
            if staged is None:
                os.symlink(target, "original", dir_fd=transaction)
                os.fsync(transaction)
            elif not expected_link(transaction, "original", target):
                raise ValueError("unexpected staged original; operator inspection required")
            # The old website and its replacement exchange atomically; neither is deleted.
            rename(web, site, transaction, "original", 2)
        if identity(info(transaction, "original")) != original_id or info(releases, "b4deployd"):
            raise ValueError(
                "backup recovery needs operator inspection; original was not overwritten"
            )
        with directory("original", transaction) as original_fd:
            marker = info(original_fd, MANIFEST)
            expected = {"name": "b4deployd", "previous": None, "created_at": journal["created_at"]}
            if marker and read_json(original_fd, MANIFEST) != expected:
                raise ValueError(
                    "unexpected backup metadata; original remains in helper recovery storage"
                )
            scan(original_fd, os.fstat(original_fd).st_dev, owner=owner, manifest=bool(marker))
            if not marker:
                write_json(original_fd, MANIFEST, expected, owner)
            os.fsync(original_fd)
        rename(transaction, "original", releases, "b4deployd", 1)
        write_json(transaction, "complete.json", {"connected": True})
        return {"status": "connected", "backup": True}


def deadline_expired(signum, frame):
    raise TimeoutError("website operation timed out; inspect its recovery journal before retrying")


def main():
    try:
        if sys.platform != "linux" or os.geteuid() != 0 or len(sys.argv) != 4:
            raise ValueError("run the installed helper through its restricted sudo rule on Linux")
        # End privileged work before the unprivileged worker's command timeout expires.
        signal.signal(signal.SIGALRM, deadline_expired)
        signal.alarm(300)
        account = pwd.getpwnam("deployd")
        if sys.argv[1] == "detach":
            result = disconnect(*sys.argv[2:], (account.pw_uid, account.pw_gid))
        else:
            result = connect(*sys.argv[1:], (account.pw_uid, account.pw_gid))
        print(json.dumps(result))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "recovery": str(STATE_ROOT)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
