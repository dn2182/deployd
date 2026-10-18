"""Fixed helper protocol; connection operations serialize with app deployments."""

import json
import os
import re
import sys
from pathlib import Path

from ..config import config_lock, delete_app_config, get_app_registry
from . import runner

HELPER = Path("/usr/local/libexec/deployd/connect-website")
SITE_ROOTS_FILE = Path("/etc/deployd/site-roots")
WEB_ROOT = Path("/var/www")


def site_roots():
    """Mirror of the helper's list, read for a clear error before sudo is involved."""
    roots = [WEB_ROOT]
    try:
        lines = SITE_ROOTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return roots
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            root = Path(line)
            if root.is_absolute() and root not in roots:
                roots.append(root)
    return roots


def arguments(name, spec, operation):
    root = Path("/srv/deployd") / name / "releases"
    if (
        sys.platform != "linux"
        or spec.release_layout != "directory"
        or spec.releases_dir != root
        or spec.current_link != root / "current"
        or not spec.site_path
        or spec.site_path.parent not in site_roots()
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", spec.site_path.name)
        or spec.restart.command != ["/usr/bin/test", "-s", f"{spec.current_link}/index.html"]
    ):
        allowed = ", ".join(str(root) for root in site_roots())
        raise ValueError(
            "automatic connection requires a managed static site directly under an allowed "
            f"site root ({allowed}); add roots to {SITE_ROOTS_FILE} and rerun the installer"
        )
    if not HELPER.is_file():
        raise ValueError("run the installer and enable the website connection helper first")
    return ["/usr/bin/sudo", "-n", str(HELPER), operation, name, str(spec.site_path)]


async def invoke(name, spec, operation):
    try:
        return await runner._run_cmd(arguments(name, spec, operation))
    except RuntimeError as exc:
        lines = str(exc).splitlines()
        try:
            result = json.loads(lines[-1])
        except (ValueError, IndexError):
            raise exc from None
        if isinstance(result, dict) and isinstance(result.get("error"), str):
            raise ValueError(result["error"]) from exc
        raise


async def inspect(name, spec):
    return json.loads(await invoke(name, spec, "check"))


async def run_connection(store, app, deploy_id, operation="connect"):
    store.set_status(deploy_id, "running")
    store.add_step(deploy_id, operation, "running")
    try:
        result = await invoke(app, get_app_registry()[app], operation)
    except Exception as exc:
        store.add_step(deploy_id, operation, "failed", output=runner._error_text(exc))
        store.set_status(deploy_id, "failed", finished=True)
        return
    store.add_step(deploy_id, operation, "succeeded", output=result)
    store.set_status(deploy_id, "succeeded", finished=True)


def link_state(spec) -> str | None:
    """linked, unlinked or missing for an app with a site path; None otherwise."""
    if not spec.site_path:
        return None
    try:
        if spec.site_path.is_symlink():
            target = Path(os.readlink(spec.site_path))
            return "linked" if target == spec.current_link else "unlinked"
        return "unlinked" if spec.site_path.exists() else "missing"
    except OSError:
        return "missing"


async def run_removal(store, app, deploy_id):
    store.set_status(deploy_id, "running")
    store.add_step(deploy_id, "restore-website", "running")
    try:
        result = await invoke(app, get_app_registry()[app], "detach")
        with config_lock():
            if not delete_app_config(app):
                raise ValueError("app configuration disappeared during removal")
    except Exception as exc:
        store.add_step(deploy_id, "restore-website", "failed", output=runner._error_text(exc))
        store.set_status(deploy_id, "failed", finished=True)
        return
    store.add_step(deploy_id, "restore-website", "succeeded", output=result)
    store.set_status(deploy_id, "succeeded", finished=True)
