import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import shutil
import signal
import socket
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import AppSpec, get_app_registry
from ..store.db import Store

log = logging.getLogger("deployd.runner")

# migrate must precede cutover: schema can't be rolled back by symlink
STEPS = ["download", "verify", "unpack", "migrate", "cutover", "restart", "health"]

CMD_TIMEOUT_SECONDS = 600
MAX_COMMAND_OUTPUT_BYTES = 1_048_576
MAX_REDIRECTS = 5
_RELEASE_NAME_RE = re.compile(r"^[0-9a-f]{40}-[0-9a-f]{32}$")


async def run_deploy(store: Store, app: str, deploy_id: str) -> None:
    spec = get_app_registry()[app]
    deploy = store.get_deploy(deploy_id)
    ctx: dict = {}
    store.set_status(deploy_id, "running")
    log.info("deploy %s: starting %s @ %s", deploy_id, app, deploy["commit_sha"][:12])

    succeeded = False
    try:
        for step in STEPS:
            store.add_step(deploy_id, step, "running")
            try:
                output = await _STEP_FNS[step](spec, deploy, ctx)
            except Exception as exc:
                store.add_step(deploy_id, step, "failed", output=_error_text(exc))
                rolled_back = await _maybe_rollback(step, spec, ctx, store, deploy_id)
                store.set_status(
                    deploy_id, "rolled_back" if rolled_back else "failed", finished=True
                )
                return
            store.add_step(deploy_id, step, "succeeded", output=output or "")

        succeeded = True
        _prune_releases(spec)
        store.set_status(deploy_id, "succeeded", finished=True)
        log.info("deploy %s: succeeded", deploy_id)
    finally:
        _cleanup_attempt(spec, ctx, succeeded=succeeded)


async def _step_download(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    incoming = spec.releases_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / f"{deploy['deploy_id']}.artifact"
    timeout = httpx.Timeout(30, read=300)
    url = deploy["artifact_url"]
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                await _validate_network_target(url, spec.artifact.allow_private_networks)
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        if redirect_count == MAX_REDIRECTS:
                            raise RuntimeError(
                                f"artifact redirect limit exceeded ({MAX_REDIRECTS})"
                            )
                        location = resp.headers.get("location")
                        if not location:
                            raise RuntimeError("artifact redirect omitted Location header")
                        redirected = urljoin(url, location)
                        if not spec.artifact.allows_redirect_url(redirected):
                            raise RuntimeError("artifact redirect target is not allowlisted")
                        url = redirected
                        continue

                    resp.raise_for_status()
                    declared = resp.headers.get("content-length")
                    if declared is not None and int(declared) > spec.artifact.max_download_bytes:
                        raise RuntimeError("artifact exceeds configured download limit")
                    written = 0
                    with dest.open("wb") as f:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            written += len(chunk)
                            if written > spec.artifact.max_download_bytes:
                                raise RuntimeError("artifact exceeds configured download limit")
                            f.write(chunk)
                    break
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    ctx["artifact_path"] = dest
    return f"{dest.stat().st_size} bytes"


async def _validate_network_target(url: str, allow_private: bool) -> None:
    if allow_private:
        return
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        raise RuntimeError("artifact URL has no hostname")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise RuntimeError(f"artifact hostname did not resolve: {host}") from exc
        addresses = list({ipaddress.ip_address(item[4][0]) for item in resolved})
    if not addresses or any(not address.is_global for address in addresses):
        raise RuntimeError("artifact URL resolves to a private or non-routable address")


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
    release_dir = spec.releases_dir / f"{deploy['commit_sha']}-{deploy['deploy_id']}"
    staging = spec.releases_dir / ".incoming" / deploy["deploy_id"]
    if staging.exists():
        shutil.rmtree(staging)
    ctx["staging_dir"] = staging
    _extract(ctx["artifact_path"], staging, spec)
    staging.rename(release_dir)
    ctx["release_dir"] = release_dir
    return str(release_dir)


def _extract(archive: Path, dest: Path, spec: AppSpec) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            entries = z.infolist()
            _validate_archive_limits(len(entries), sum(entry.file_size for entry in entries), spec)
            base = dest.resolve()
            seen = set()
            for entry in entries:
                name = entry.filename
                if name in seen:
                    raise RuntimeError(f"archive contains duplicate entry: {name}")
                seen.add(name)
                if entry.flag_bits & 0x1:
                    raise RuntimeError(f"encrypted archive entry is not supported: {name}")
                mode = entry.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise RuntimeError(f"archive contains a special file: {name}")
                if not (dest / name).resolve().is_relative_to(base):
                    raise RuntimeError(f"archive entry escapes release dir: {name}")
            z.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            entries = t.getmembers()
            _validate_archive_limits(len(entries), sum(entry.size for entry in entries), spec)
            for entry in entries:
                if not (entry.isfile() or entry.isdir()):
                    raise RuntimeError(f"archive contains a link or special file: {entry.name}")
            t.extractall(dest, filter="data")
    else:
        raise RuntimeError("unsupported artifact format (zip or tar expected)")


def _validate_archive_limits(file_count: int, total_bytes: int, spec: AppSpec) -> None:
    if file_count > spec.artifact.max_extract_files:
        raise RuntimeError("artifact exceeds configured extracted-file limit")
    if total_bytes > spec.artifact.max_extract_bytes:
        raise RuntimeError("artifact exceeds configured extracted-size limit")


async def _step_migrate(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    if not spec.migrate.command:
        return "no migration configured"
    return await _run_cmd(spec.migrate.command, cwd=ctx["release_dir"])


async def _step_cutover(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    link = spec.current_link
    ctx["previous_release"] = _current_target(link)
    _atomic_symlink(ctx["release_dir"], link)
    return f"current -> {ctx['release_dir'].name}"


def _current_target(link: Path) -> Path | None:
    # os.readlink resolves both POSIX symlinks and Windows junctions
    try:
        target = Path(os.readlink(link))
        if not target.is_absolute():
            target = link.parent / target
        return target.resolve(strict=False)
    except OSError:
        return None


def _atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target = target.resolve(strict=False)
    tmp = link.with_name(link.name + ".new")
    if os.name == "nt":
        # junctions need no privilege on Windows, unlike symlinks; rename can't
        # overwrite a directory link, so there is a brief window with no link
        import _winapi

        if os.path.lexists(tmp):
            os.rmdir(tmp)
        _winapi.CreateJunction(str(target), str(tmp))
        if os.path.lexists(link):
            os.rmdir(link)
        os.rename(tmp, link)
        return
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, link)


async def _step_restart(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    return await _run_cmd(spec.restart.command)


async def _step_health(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    last_error = "no attempts made"
    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(1, spec.health.retries + 1):
            try:
                resp = await client.get(spec.health.url)
                if 200 <= resp.status_code < 300:
                    return f"healthy after {attempt} attempt(s)"
                last_error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_error = exc.__class__.__name__
            if attempt < spec.health.retries:
                await asyncio.sleep(spec.health.interval_seconds)
    raise RuntimeError(f"unhealthy after {spec.health.retries} attempts: {last_error}")


async def _run_cmd(command: list[str], cwd: Path | None = None) -> str:
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    try:
        output = await asyncio.wait_for(_read_bounded_output(proc), timeout=CMD_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        await asyncio.shield(_kill_process_tree(proc))
        raise
    except TimeoutError as exc:
        await _kill_process_tree(proc)
        raise RuntimeError(f"configured command timed out after {CMD_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"configured command exited {proc.returncode}\n{output}")
    return output


async def _read_bounded_output(proc: asyncio.subprocess.Process) -> str:
    kept = bytearray()
    total = 0
    while True:
        chunk = await proc.stdout.read(1 << 16)
        if not chunk:
            break
        total += len(chunk)
        if len(kept) < MAX_COMMAND_OUTPUT_BYTES:
            kept.extend(chunk[: MAX_COMMAND_OUTPUT_BYTES - len(kept)])
    await proc.wait()
    output = kept.decode(errors="replace").strip()
    if total > len(kept):
        output += f"\n[output truncated; {total - len(kept)} bytes omitted]"
    return output


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    await proc.wait()


async def _maybe_rollback(
    failed_step: str, spec: AppSpec, ctx: dict, store: Store, deploy_id: str
) -> bool:
    # failures before cutover never touched the running version
    if failed_step not in ("cutover", "restart", "health"):
        return False
    previous = ctx.get("previous_release")
    if previous is None:
        release = ctx.get("release_dir")
        current = _current_target(spec.current_link)
        if release is not None and current is not None and release.resolve() == current:
            _remove_link(spec.current_link)
            store.add_step(
                deploy_id,
                "rollback",
                "succeeded",
                output="removed failed first release; no previous release existed",
            )
        else:
            store.add_step(deploy_id, "rollback", "skipped", output="no previous release")
        return False
    store.add_step(deploy_id, "rollback", "running")
    try:
        _atomic_symlink(previous, spec.current_link)
        await _run_cmd(spec.restart.command)
        await _STEP_FNS["health"](spec, {}, {**ctx, "rollback_verification": True})
    except Exception as exc:
        store.add_step(deploy_id, "rollback", "failed", output=_error_text(exc))
        return False
    store.add_step(
        deploy_id,
        "rollback",
        "succeeded",
        output=f"reverted to {previous.name}; health verified",
    )
    return True


def _remove_link(link: Path) -> None:
    if os.name == "nt":
        if os.path.lexists(link):
            os.rmdir(link)
    elif link.is_symlink():
        link.unlink()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"artifact server returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return f"artifact request failed: {exc.__class__.__name__}"
    return str(exc)


def _cleanup_attempt(spec: AppSpec, ctx: dict, *, succeeded: bool) -> None:
    try:
        artifact = ctx.get("artifact_path")
        if artifact and artifact.exists():
            artifact.unlink()
        staging = ctx.get("staging_dir")
        if staging and staging.exists():
            shutil.rmtree(staging)
        release = ctx.get("release_dir")
        current = _current_target(spec.current_link)
        if (
            not succeeded
            and release
            and release.exists()
            and (current is None or release.resolve() != current)
        ):
            shutil.rmtree(release)
    except OSError:
        log.warning("deployment-attempt cleanup failed", exc_info=True)


def _prune_releases(spec: AppSpec) -> None:
    try:
        releases = sorted(
            (
                p
                for p in spec.releases_dir.iterdir()
                if p.is_dir() and _RELEASE_NAME_RE.fullmatch(p.name)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        current = _current_target(spec.current_link)
        keep = set()
        if current is not None:
            keep.add(current)
        for release in releases:
            if len(keep) >= spec.keep_releases:
                break
            keep.add(release.resolve())
        for release in releases:
            if release.resolve() not in keep:
                shutil.rmtree(release)
    except OSError:
        log.warning("release pruning failed", exc_info=True)


_STEP_FNS = {
    "download": _step_download,
    "verify": _step_verify,
    "unpack": _step_unpack,
    "migrate": _step_migrate,
    "cutover": _step_cutover,
    "restart": _step_restart,
    "health": _step_health,
}
