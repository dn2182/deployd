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

from ..config import AppSpec, get_app_registry, get_settings
from ..store.db import Store
from . import directory_layout

log = logging.getLogger("deployd.runner")

# migrate must precede cutover: schema can't be rolled back by symlink
STEPS = ["download", "verify", "unpack", "migrate", "cutover", "restart", "health"]

CMD_TIMEOUT_SECONDS = 600
MAX_COMMAND_OUTPUT_BYTES = 1_048_576
MAX_REDIRECTS = 5
_RELEASE_NAME_RE = re.compile(r"^[0-9a-f]{40}-[0-9a-f]{32}$")
_GITHUB_ASSET_PATH_RE = re.compile(
    r"/repos/[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*/releases/assets/[0-9]+"
)


def _github_asset_headers(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not _GITHUB_ASSET_PATH_RE.fullmatch(parsed.path)
    ):
        return {}
    headers = {"Accept": "application/octet-stream", "X-GitHub-Api-Version": "2022-11-28"}
    token = get_settings().github_token
    if token and token.get_secret_value():
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    return headers


async def run_deploy(store: Store, app: str, deploy_id: str) -> None:
    spec = get_app_registry()[app]
    if spec.release_layout == "directory":
        directory_layout.prepare(spec)
        directory_layout.reconcile(spec)
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
        if spec.auto_cleanup:
            _prune_releases(spec)
        store.set_status(deploy_id, "succeeded", finished=True)
        log.info("deploy %s: succeeded", deploy_id)
    finally:
        _cleanup_attempt(spec, ctx, succeeded=succeeded)


async def _step_download(spec: AppSpec, deploy: dict, ctx: dict) -> str:
    url = deploy["artifact_url"]
    if not spec.artifact.allows_initial_url(url):
        raise RuntimeError("artifact URL is not allowlisted")
    headers = _github_asset_headers(url)
    incoming = spec.releases_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / f"{deploy['deploy_id']}.artifact"
    timeout = httpx.Timeout(30, read=300)
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                await _validate_network_target(url, spec.artifact.allow_private_networks)
                async with client.stream("GET", url, headers=headers) as resp:
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
                        # GitHub redirects to signed storage URLs; never forward the API token.
                        headers = {}
                        continue

                    if "Authorization" in headers and resp.status_code in (401, 403, 404):
                        raise RuntimeError(
                            f"GitHub release download returned HTTP {resp.status_code}; check "
                            "DEPLOYD_GITHUB_TOKEN expiry and Contents read access to the repository"
                        )
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
    os.utime(release_dir, None)
    if spec.release_layout == "directory":
        directory_layout.initialize(release_dir)
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
    if spec.release_layout == "directory":
        return directory_layout.cutover(spec, ctx["release_dir"], ctx)
    link = spec.current_link
    ctx["previous_release"] = _current_target(link)
    ctx["older_previous"] = _current_target(_previous_link(spec))
    if ctx["previous_release"] is not None:
        _atomic_symlink(ctx["previous_release"], _previous_link(spec))
    _atomic_symlink(ctx["release_dir"], link)
    return f"current -> {ctx['release_dir'].name}"


def current_release_path(spec: AppSpec) -> Path | None:
    if spec.release_layout == "directory":
        return spec.current_link if spec.current_link.is_dir() else None
    return _current_target(spec.current_link)


def _current_target(link: Path) -> Path | None:
    # os.readlink resolves both POSIX symlinks and Windows junctions
    try:
        target = Path(os.readlink(link))
        if not target.is_absolute():
            target = link.parent / target
        return target.resolve(strict=False)
    except OSError:
        return None


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        os.name == "nt"
        and os.path.lexists(path)
        and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    )


def _atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target = target.resolve(strict=False)
    tmp = link.with_name(link.name + ".new")
    for candidate in (link, tmp):
        if os.path.lexists(candidate) and not _is_link(candidate):
            raise RuntimeError(f"refusing to replace non-link path: {candidate}")
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
    if spec.release_layout == "directory":
        transaction = ctx.get("directory_transaction")
        if not transaction or not transaction["switched"]:
            return False
        store.add_step(deploy_id, "rollback", "running")
        try:
            restored = directory_layout.restore(spec, ctx)
            if not restored:
                store.add_step(
                    deploy_id,
                    "rollback",
                    "skipped",
                    output="failed first release moved aside; no prior version",
                )
                return False
            await _run_cmd(spec.restart.command)
            await _STEP_FNS["health"](spec, {}, {**ctx, "rollback_verification": True})
        except Exception as exc:
            store.add_step(deploy_id, "rollback", "failed", output=_error_text(exc))
            return False
        store.add_step(
            deploy_id,
            "rollback",
            "succeeded",
            output="previous directory restored; health verified",
        )
        return True
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
        if ctx.get("older_previous") is not None:
            _atomic_symlink(ctx["older_previous"], _previous_link(spec))
        else:
            _remove_link(_previous_link(spec))
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
    if os.path.lexists(link) and not _is_link(link):
        raise RuntimeError(f"refusing to remove non-link path: {link}")
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
            and "directory_transaction" not in ctx
            and release
            and release.exists()
            and (current is None or release.resolve() != current)
        ):
            shutil.rmtree(release)
    except OSError:
        log.warning("deployment-attempt cleanup failed", exc_info=True)


def _prune_releases(spec: AppSpec) -> None:
    try:
        if spec.release_layout == "directory":
            directory_layout.prune(spec)
            return
        releases = _managed_releases(spec)
        current = _current_target(spec.current_link)
        if current is None or not current.is_dir():
            return
        keep = set()
        keep.add(current)
        previous = _current_target(_previous_link(spec))
        if previous is not None and spec.keep_previous > 0:
            keep.add(previous)
        for release in releases:
            if len(keep) >= spec.keep_previous + 1:
                break
            keep.add(release.resolve())
        for release in releases:
            if release.resolve() not in keep:
                shutil.rmtree(release)
        if spec.keep_previous == 0:
            _remove_link(_previous_link(spec))
    except (OSError, ValueError):
        log.warning("release pruning failed", exc_info=True)


def _previous_link(spec: AppSpec) -> Path:
    return spec.current_link.with_name(spec.current_link.name + ".previous")


def _managed_releases(spec: AppSpec) -> list[Path]:
    if not spec.releases_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in spec.releases_dir.iterdir()
            if _RELEASE_NAME_RE.fullmatch(path.name) and not _is_link(path) and path.is_dir()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def local_release(spec: AppSpec, name: str) -> Path:
    if spec.release_layout == "directory":
        return directory_layout.local_release(spec, name)
    if name == "previous":
        path = _current_target(_previous_link(spec))
        if path is None or not path.is_dir():
            raise ValueError("previous release is unavailable")
        return path
    if not _RELEASE_NAME_RE.fullmatch(name):
        raise ValueError("invalid release name")
    path = spec.releases_dir / name
    if _is_link(path) or not path.is_dir() or path.resolve().parent != spec.releases_dir.resolve():
        raise ValueError("local release is unavailable")
    return path


def list_releases(spec: AppSpec) -> dict:
    if spec.release_layout == "directory":
        return directory_layout.records(spec)
    current = _current_target(spec.current_link)
    previous = _current_target(_previous_link(spec))
    paths = _managed_releases(spec)
    releases = []
    for path in paths:
        resolved = path.resolve()
        releases.append(
            {
                "name": path.name,
                "commit_sha": path.name[:40],
                "active": resolved == current,
                "previous": resolved == previous,
                "protected": resolved == current
                or (resolved == previous and spec.keep_previous != 0),
                "created_at": path.stat().st_mtime,
            }
        )
    if previous and previous.is_dir() and not any(item["previous"] for item in releases):
        releases.append(
            {
                "name": "previous",
                "commit_sha": None,
                "active": previous == current,
                "previous": True,
                "protected": True,
                "created_at": previous.stat().st_mtime,
            }
        )
    return {
        "active_path": str(current) if current else None,
        "previous_path": str(previous) if previous else None,
        "releases": releases,
    }


def remove_release(spec: AppSpec, name: str) -> None:
    if spec.release_layout == "directory":
        directory_layout.remove_release(spec, name)
        return
    if name == "previous":
        raise ValueError("the previous release is protected")
    target = local_release(spec, name)
    protected = {_current_target(spec.current_link)}
    if spec.keep_previous != 0:
        protected.add(_current_target(_previous_link(spec)))
    if target.resolve() in protected:
        raise ValueError("active and previous releases are protected")
    shutil.rmtree(target)
    if target.resolve() == _current_target(_previous_link(spec)):
        _remove_link(_previous_link(spec))


async def run_activation(store: Store, app: str, deploy_id: str, name: str) -> None:
    spec = get_app_registry()[app]
    deploy = store.get_deploy(deploy_id)
    store.set_status(deploy_id, "running")
    try:
        ctx = {"release_dir": local_release(spec, name)}
        if current_release_path(spec) == ctx["release_dir"].resolve():
            raise ValueError("release is already active")
    except ValueError as exc:
        store.add_step(deploy_id, "activation", "failed", output=str(exc))
        store.set_status(deploy_id, "failed", finished=True)
        return
    for step in ("cutover", "restart", "health"):
        store.add_step(deploy_id, step, "running")
        try:
            output = await _STEP_FNS[step](spec, deploy, ctx)
        except Exception as exc:
            store.add_step(deploy_id, step, "failed", output=_error_text(exc))
            rolled_back = await _maybe_rollback(step, spec, ctx, store, deploy_id)
            store.set_status(deploy_id, "rolled_back" if rolled_back else "failed", finished=True)
            return
        store.add_step(deploy_id, step, "succeeded", output=output or "")
    store.set_status(deploy_id, "succeeded", finished=True)


_STEP_FNS = {
    "download": _step_download,
    "verify": _step_verify,
    "unpack": _step_unpack,
    "migrate": _step_migrate,
    "cutover": _step_cutover,
    "restart": _step_restart,
    "health": _step_health,
}
