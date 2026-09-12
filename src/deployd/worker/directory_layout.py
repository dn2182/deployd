"""Real current directories with release identities that travel with their files."""

import ctypes
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

from ..config import AppSpec

MANIFEST = ".deployd-release.json"
BASELINE = "b4deployd"
RELEASE_NAME = re.compile(r"^(?:[0-9a-f]{40}-[0-9a-f]{32}|b4deployd)$")


def is_link(path: Path) -> bool:
    return path.is_symlink() or (
        os.name == "nt"
        and os.path.lexists(path)
        and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    )


def exchange(left: Path, right: Path) -> None:
    """No two-rename fallback: current must never disappear during replacement."""
    for path in (left, right):
        if is_link(path) or not path.is_dir():
            raise ValueError("directory exchange requires two real directories")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux" and hasattr(libc, "renameat2"):
        swap = libc.renameat2
        swap.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        swap.restype = ctypes.c_int
        result = swap(-100, os.fsencode(left), -100, os.fsencode(right), 2)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        swap = libc.renamex_np
        swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        swap.restype = ctypes.c_int
        result = swap(os.fsencode(left), os.fsencode(right), 2)
    else:
        raise RuntimeError("atomic directory exchange is unavailable; use symlink layout")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, f"atomic directory exchange failed: {os.strerror(error)}")


def read_manifest(path: Path) -> dict:
    marker = path / MANIFEST
    if is_link(path) or not path.is_dir() or is_link(marker) or not marker.is_file():
        raise ValueError(f"unmanaged release directory: {path.name}")
    if marker.stat().st_size > 4096:
        raise ValueError(f"invalid release metadata: {path.name}")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        valid = (
            isinstance(data, dict)
            and isinstance(data.get("name"), str)
            and RELEASE_NAME.fullmatch(data["name"])
            and (
                data.get("previous") is None
                or (isinstance(data["previous"], str) and RELEASE_NAME.fullmatch(data["previous"]))
            )
            and isinstance(data.get("created_at"), (int, float))
            and math.isfinite(data["created_at"])
        )
        if not valid:
            raise ValueError("invalid fields")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid release metadata: {path.name}") from exc
    return data


def write_manifest(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path, prefix=".deployd-", delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
            os.replace(temporary, path / MANIFEST)
        finally:
            temporary.unlink(missing_ok=True)


def initialize(path: Path) -> None:
    if os.path.lexists(path / MANIFEST):
        raise ValueError(f"artifact contains reserved file {MANIFEST}")
    write_manifest(path, {"name": path.name, "previous": None, "created_at": time.time()})


def prepare(spec: AppSpec) -> None:
    if sys.platform not in ("linux", "darwin"):
        raise RuntimeError("directory layout requires Linux or macOS; use symlink layout here")
    missing = []
    path = spec.releases_dir
    while not os.path.lexists(path):
        missing.append(path)
        path = path.parent
    if is_link(spec.releases_dir):
        raise ValueError("releases_dir must be a real directory")
    for path in reversed(missing):
        path.mkdir()
        path.chmod(0o755)
    # Probe the actual filesystem before accepting a layout that needs atomic exchange.
    with tempfile.TemporaryDirectory(prefix=".deployd-check-", dir=spec.releases_dir) as probe:
        left, right = Path(probe) / "left", Path(probe) / "right"
        left.mkdir()
        right.mkdir()
        exchange(left, right)


def archives(spec: AppSpec) -> list[Path]:
    if is_link(spec.releases_dir):
        raise ValueError("releases_dir must be a real directory")
    if not spec.releases_dir.exists():
        return []
    return [
        p
        for p in spec.releases_dir.iterdir()
        if RELEASE_NAME.fullmatch(p.name) and not is_link(p) and p.is_dir()
    ]


def reconcile(spec: AppSpec) -> None:
    """Finish archive naming after a process died between exchange and rename."""
    if is_link(spec.releases_dir):
        raise ValueError("releases_dir must be a real directory")
    current = read_manifest(spec.current_link) if os.path.lexists(spec.current_link) else None
    for path in archives(spec):
        data = read_manifest(path)
        if current and data["name"] == current["name"]:
            raise ValueError(
                "duplicate active release identity; inspect directories before retrying"
            )
        if path.name != data["name"]:
            target = spec.releases_dir / data["name"]
            if os.path.lexists(target):
                raise ValueError("archive recovery would overwrite a directory; inspect releases")
            path.rename(target)


def records(spec: AppSpec) -> dict:
    current = read_manifest(spec.current_link) if os.path.lexists(spec.current_link) else None
    previous = current.get("previous") if current else None
    rows = []
    for path in archives(spec):
        data = read_manifest(path)
        if data["name"] != path.name or (current and data["name"] == current["name"]):
            raise ValueError(
                "release layout needs recovery; restart deployd before changing versions"
            )
        rows.append(
            {
                "name": path.name,
                "release_id": data["name"],
                "commit_sha": None if data["name"] == BASELINE else data["name"][:40],
                "created_at": data["created_at"],
                "active": False,
                "previous": path.name == previous,
                "protected": path.name == previous and spec.keep_previous > 0,
            }
        )
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    if current:
        rows.insert(
            0,
            {
                "name": "current",
                "release_id": current["name"],
                "commit_sha": None if current["name"] == BASELINE else current["name"][:40],
                "created_at": current["created_at"],
                "active": True,
                "previous": False,
                "protected": True,
            },
        )
    prior = next((row for row in rows if row["previous"]), None)
    return {
        "active_path": str(spec.current_link) if current else None,
        "previous_path": str(spec.releases_dir / prior["name"]) if prior else None,
        "releases": rows,
    }


def local_release(spec: AppSpec, name: str) -> Path:
    if not RELEASE_NAME.fullmatch(name):
        raise ValueError("select a retained release by its version name")
    path = spec.releases_dir / name
    data = read_manifest(path)
    if data["name"] != name:
        raise ValueError("release identity mismatch; restart deployd to recover archive naming")
    return path


def cutover(spec: AppSpec, target: Path, ctx: dict) -> str:
    reconcile(spec)
    new = read_manifest(target)
    current = spec.current_link
    old = read_manifest(current) if os.path.lexists(current) else None
    destination = spec.releases_dir / old["name"] if old else None
    if destination and os.path.lexists(destination):
        raise ValueError("previous release destination already exists")
    new["previous"] = old["name"] if old else None
    write_manifest(target, new)
    transaction = {"target": target, "old_path": None, "switched": False}
    ctx["directory_transaction"] = transaction
    if old:
        exchange(current, target)
        transaction.update(switched=True, old_path=target)
        target.rename(destination)
        transaction["old_path"] = destination
    else:
        # A first deployment has nothing to exchange or roll back to.
        target.rename(current)
        transaction["switched"] = True
    return f"current contains {new['name']}"


def restore(spec: AppSpec, ctx: dict) -> bool:
    transaction = ctx.get("directory_transaction")
    if not transaction or not transaction["switched"]:
        return False
    target, old = transaction["target"], transaction["old_path"]
    if old:
        exchange(spec.current_link, old)
        transaction["switched"] = False
        if old != target:
            if os.path.lexists(target):
                raise ValueError("rollback archive destination already exists")
            old.rename(target)
        return True
    if os.path.lexists(target):
        raise ValueError("failed first release cannot be moved aside safely")
    spec.current_link.rename(target)
    transaction["switched"] = False
    return False


def remove_release(spec: AppSpec, name: str) -> None:
    path = local_release(spec, name)
    row = next(row for row in records(spec)["releases"] if row["name"] == name)
    if row["protected"]:
        raise ValueError("the previous release is protected")
    shutil.rmtree(path)


def prune(spec: AppSpec) -> None:
    data = records(spec)
    if not data["active_path"]:
        return
    # The pre-deployd site is an explicit recovery point, outside normal retention.
    retained = [
        row for row in data["releases"] if not row["active"] and row["release_id"] != BASELINE
    ]
    keep = {row["name"] for row in retained if row["protected"]}
    for row in retained:
        if len(keep) >= spec.keep_previous:
            break
        keep.add(row["name"])
    for row in retained:
        if row["name"] not in keep:
            remove_release(spec, row["name"])
