import asyncio
import hashlib
import logging
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

import httpx

from ..config import AppSpec, get_app_registry
from ..store.db import Store

log = logging.getLogger("deployd.runner")

# migrate must precede cutover: schema can't be rolled back by symlink
STEPS = ["download", "verify", "unpack", "migrate", "cutover", "restart", "health"]

CMD_TIMEOUT_SECONDS = 600


async def run_deploy(store: Store, app: str, deploy_id: str) -> None:
    spec = get_app_registry()[app]
    deploy = store.get_deploy(deploy_id)
    ctx: dict = {}
    store.set_status(deploy_id, "running")
    log.info("deploy %s: starting %s @ %s", deploy_id, app, deploy["commit_sha"][:12])

    for step in STEPS:
        store.add_step(deploy_id, step, "running")
        try:
            output = await _STEP_FNS[step](spec, deploy, ctx)
        except Exception as exc:
            store.add_step(deploy_id, step, "failed", output=str(exc))
            rolled_back = await _maybe_rollback(step, spec, ctx, store, deploy_id)
            store.set_status(
                deploy_id, "rolled_back" if rolled_back else "failed", finished=True
            )
            return
        store.add_step(deploy_id, step, "succeeded", output=output or "")

    _cleanup(spec, ctx)
    store.set_status(deploy_id, "succeeded", finished=True)
    log.info("deploy %s: succeeded", deploy_id)


async def _step_download(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    incoming = spec.releases_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / f"{deploy['commit_sha']}.artifact"
    timeout = httpx.Timeout(30, read=300)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", deploy["artifact_url"]) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
    ctx["artifact_path"] = dest
    return f"{dest.stat().st_size} bytes"


async def _step_verify(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    digest = hashlib.sha256()
    with ctx["artifact_path"].open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != deploy["artifact_sha256"]:
        raise RuntimeError(
            f"sha256 mismatch: expected {deploy['artifact_sha256']}, got {digest.hexdigest()}"
        )
    return digest.hexdigest()


async def _step_unpack(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    release_dir = spec.releases_dir / deploy["commit_sha"]
    if release_dir.exists():
        shutil.rmtree(release_dir)
    staging = spec.releases_dir / ".incoming" / deploy["commit_sha"]
    if staging.exists():
        shutil.rmtree(staging)
    _extract(ctx["artifact_path"], staging)
    staging.rename(release_dir)
    ctx["release_dir"] = release_dir
    return str(release_dir)


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            base = dest.resolve()
            for name in z.namelist():
                if not (dest / name).resolve().is_relative_to(base):
                    raise RuntimeError(f"archive entry escapes release dir: {name}")
            z.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    else:
        raise RuntimeError("unsupported artifact format (zip or tar expected)")


async def _step_migrate(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    if not spec.migrate.command:
        return "no migration configured"
    return await _run_cmd(spec.migrate.command, cwd=ctx["release_dir"])


async def _step_cutover(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    link = spec.current_link
    ctx["previous_release"] = Path(os.readlink(link)) if link.is_symlink() else None
    _atomic_symlink(ctx["release_dir"], link)
    return f"current -> {ctx['release_dir'].name}"


def _atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(link.name + ".new")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, link)


async def _step_restart(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    return await _run_cmd(spec.restart.command)


async def _step_health(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    last_error = "no attempts made"
    for attempt in range(1, spec.health.retries + 1):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(spec.health.url)
            if 200 <= resp.status_code < 300:
                return f"healthy after {attempt} attempt(s)"
            last_error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        await asyncio.sleep(spec.health.interval_seconds)
    raise RuntimeError(f"unhealthy after {spec.health.retries} attempts: {last_error}")


async def _run_cmd(command: list[str], cwd: Path | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CMD_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"command timed out after {CMD_TIMEOUT_SECONDS}s: {command}")
    output = stdout.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"command exited {proc.returncode}: {command}\n{output}")
    return output


async def _maybe_rollback(
    failed_step: str, spec: AppSpec, ctx: dict, store: Store, deploy_id: str
) -> bool:
    # failures before cutover never touched the running version
    if failed_step not in ("restart", "health"):
        return False
    previous = ctx.get("previous_release")
    if previous is None:
        store.add_step(deploy_id, "rollback", "skipped", output="no previous release")
        return False
    store.add_step(deploy_id, "rollback", "running")
    try:
        _atomic_symlink(previous, spec.current_link)
        await _run_cmd(spec.restart.command)
    except Exception as exc:
        store.add_step(deploy_id, "rollback", "failed", output=str(exc))
        return False
    store.add_step(deploy_id, "rollback", "succeeded", output=f"reverted to {previous.name}")
    return True


def _cleanup(spec: AppSpec, ctx: dict) -> None:
    try:
        artifact = ctx.get("artifact_path")
        if artifact and artifact.exists():
            artifact.unlink()
        releases = sorted(
            (p for p in spec.releases_dir.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        current = spec.current_link.resolve() if spec.current_link.exists() else None
        for old in releases[spec.keep_releases :]:
            if current and old.resolve() == current:
                continue
            shutil.rmtree(old)
    except OSError:
        log.warning("release cleanup failed", exc_info=True)


_STEP_FNS = {
    "download": _step_download,
    "verify": _step_verify,
    "unpack": _step_unpack,
    "migrate": _step_migrate,
    "cutover": _step_cutover,
    "restart": _step_restart,
    "health": _step_health,
}
