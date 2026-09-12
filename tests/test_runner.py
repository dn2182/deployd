import asyncio
import hashlib
import io
import shutil
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import runner

SHA_V1 = "a" * 40
SHA_V2 = "b" * 40

GITHUB_ASSET_URL = "https://api.github.com/repos/acme/site/releases/assets/123"


@pytest.mark.parametrize("token", [None, "", "github_pat_test_secret"])
def test_github_asset_headers(token, monkeypatch):
    monkeypatch.delenv("DEPLOYD_GITHUB_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", token)
    headers = runner._github_asset_headers(GITHUB_ASSET_URL)
    assert headers["Accept"] == "application/octet-stream"
    if token:
        assert headers["Authorization"] == f"Bearer {token}"
    else:
        assert "Authorization" not in headers


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/repos/acme/site/releases/assets/123",
        "https://api.github.com:8443/repos/acme/site/releases/assets/123",
        "https://api.github.com.evil.example/repos/acme/site/releases/assets/123",
        "https://api.github.com@evil.example/repos/acme/site/releases/assets/123",
        "https://user@api.github.com/repos/acme/site/releases/assets/123",
        "https://api.github.com/repos/../site/releases/assets/123",
        "https://api.github.com/repos/acme/site/releases/assets/%31%32%33",
        GITHUB_ASSET_URL + "?download=1",
        GITHUB_ASSET_URL + "#fragment",
        "https://api.github.com/repos/acme/site/contents/index.html",
        "https://github.com/acme/site/releases/download/v1/app.zip",
        "https://release-assets.githubusercontent.com/file.zip",
    ],
)
def test_github_headers_are_restricted_to_asset_api(url, monkeypatch):
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "github_pat_test_secret")
    assert runner._github_asset_headers(url) == {}


def make_artifact(tmp_path: Path, name: str, content: str) -> tuple[Path, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.txt", content)
    path = tmp_path / name
    path.write_bytes(buf.getvalue())
    return path, hashlib.sha256(buf.getvalue()).hexdigest()


@pytest.fixture
def spec(tmp_path):
    return AppSpec.model_validate(
        {
            "releases_dir": str(tmp_path / "releases"),
            "current_link": str(tmp_path / "current"),
            "keep_releases": 5,
            "artifact": {"allowed_url_prefix": "https://example.com/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/healthz", "retries": 1, "interval_seconds": 0},
        }
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    s.init()
    return s


def wire(monkeypatch, spec, artifact_path, *, healthy=True):
    monkeypatch.setattr(runner, "get_app_registry", lambda: {"app-x": spec})

    async def fake_download(spec_, deploy, ctx):
        dest = spec_.releases_dir / ".incoming" / f"{deploy['commit_sha']}.artifact"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(artifact_path, dest)
        ctx["artifact_path"] = dest
        return "copied"

    async def fake_health(spec_, deploy, ctx):
        if not healthy and not ctx.get("rollback_verification"):
            raise RuntimeError("unhealthy")
        return "healthy"

    monkeypatch.setitem(runner._STEP_FNS, "download", fake_download)
    monkeypatch.setitem(runner._STEP_FNS, "health", fake_health)


def new_deploy(store, sha, digest):
    return store.create_deploy("app-x", sha, "https://example.com/a.zip", digest, "test")


async def test_full_deploy_succeeds(tmp_path, spec, store, monkeypatch):
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    did = new_deploy(store, SHA_V1, digest)

    await runner.run_deploy(store, "app-x", did)

    row = store.get_deploy(did)
    assert row["status"] == "succeeded"
    assert spec.current_link.resolve().parent == spec.releases_dir.resolve()
    assert spec.current_link.resolve().name.startswith(f"{SHA_V1}-")
    assert (spec.current_link / "app.txt").read_text() == "v1"
    succeeded = [s["step"] for s in row["steps"] if s["status"] == "succeeded"]
    assert succeeded == runner.STEPS


@pytest.mark.parametrize("layout", ["symlink", "directory"])
@pytest.mark.parametrize("empty_index", [False, True])
async def test_static_deploy_activation_and_rollback_without_http_health(
    tmp_path, spec, store, monkeypatch, layout, empty_index
):
    spec.release_layout = layout
    if layout == "directory":
        spec.current_link = spec.releases_dir / "current"
    spec.health.url = None
    spec.restart.command = [shutil.which("test"), "-s", str(spec.current_link / "index.html")]

    def no_http(*args, **kwargs):
        pytest.fail("HTTP must not be attempted without a configured health URL")

    monkeypatch.setattr(httpx, "AsyncClient", no_http)
    deployments = []
    for sha in (SHA_V1, SHA_V2):
        artifact = tmp_path / f"{sha}.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("index.html", sha)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        wire(monkeypatch, spec, artifact)
        monkeypatch.setitem(runner._STEP_FNS, "health", runner._step_health)
        did = new_deploy(store, sha, digest)
        deployments.append(did)
        await runner.run_deploy(store, "app-x", did)
        row = store.get_deploy(did)
        assert row["status"] == "succeeded"
        health = next(step for step in row["steps"] if step["step"] == "health")
        assert health["status"] == "skipped" and "no URL configured" in health["output"]

    activation = new_deploy(store, SHA_V1, "c" * 64)
    await runner.run_activation(store, "app-x", activation, f"{SHA_V1}-{deployments[0]}")
    row = store.get_deploy(activation)
    assert row["status"] == "succeeded"
    assert next(step for step in row["steps"] if step["step"] == "health")["status"] == "skipped"
    assert (spec.current_link / "index.html").read_text() == SHA_V1

    artifact, digest = make_artifact(tmp_path, "missing-index.zip", "not an index page")
    if empty_index:
        with zipfile.ZipFile(artifact, "a") as archive:
            archive.writestr("index.html", "")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    wire(monkeypatch, spec, artifact)
    monkeypatch.setitem(runner._STEP_FNS, "health", runner._step_health)
    failed = new_deploy(store, "c" * 40, digest)
    await runner.run_deploy(store, "app-x", failed)
    row = store.get_deploy(failed)
    assert row["status"] == "rolled_back"
    rollback = next(step for step in row["steps"] if step["step"] == "rollback")
    assert "HTTP health check skipped" in rollback["output"]
    assert "health verified" not in rollback["output"]
    assert (spec.current_link / "index.html").read_text() == SHA_V1


@pytest.mark.parametrize("status", [200, 503])
async def test_configured_http_health_is_still_checked(spec, monkeypatch, status):
    requested = []
    real_client = httpx.AsyncClient

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(status)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    if status == 200:
        assert await runner._step_health(spec, {}, {}) == "healthy after 1 attempt(s)"
    else:
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await runner._step_health(spec, {}, {})
    assert requested == [spec.health.url]


async def test_sha_mismatch_fails_before_cutover(tmp_path, spec, store, monkeypatch):
    artifact, _ = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    did = new_deploy(store, SHA_V1, "0" * 64)

    await runner.run_deploy(store, "app-x", did)

    row = store.get_deploy(did)
    assert row["status"] == "failed"
    assert not spec.current_link.exists()
    failed = [s for s in row["steps"] if s["status"] == "failed"]
    assert failed[0]["step"] == "verify"


async def test_health_failure_rolls_back_to_previous(tmp_path, spec, store, monkeypatch):
    artifact1, digest1 = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact1)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest1))
    previous = spec.current_link.resolve()

    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2, healthy=False)
    did2 = new_deploy(store, SHA_V2, digest2)
    await runner.run_deploy(store, "app-x", did2)

    row = store.get_deploy(did2)
    assert row["status"] == "rolled_back"
    assert spec.current_link.resolve() == previous
    assert (spec.current_link / "app.txt").read_text() == "v1"


async def test_health_failure_without_previous_release_fails(tmp_path, spec, store, monkeypatch):
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact, healthy=False)
    did = new_deploy(store, SHA_V1, digest)

    await runner.run_deploy(store, "app-x", did)

    row = store.get_deploy(did)
    assert row["status"] == "failed"
    rollback = [s for s in row["steps"] if s["step"] == "rollback"]
    assert rollback[0]["status"] == "succeeded"
    assert not spec.current_link.exists()


async def test_migration_failure_halts_without_touching_current(tmp_path, spec, store, monkeypatch):
    artifact1, digest1 = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact1)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest1))
    previous = spec.current_link.resolve()

    spec.migrate.command = ["false"]
    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2)
    did2 = new_deploy(store, SHA_V2, digest2)
    await runner.run_deploy(store, "app-x", did2)

    row = store.get_deploy(did2)
    assert row["status"] == "failed"
    assert spec.current_link.resolve() == previous


def test_zip_path_traversal_rejected(tmp_path, spec):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "pwned")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buf.getvalue())

    with pytest.raises(RuntimeError, match="escapes release dir"):
        runner._extract(archive, tmp_path / "out", spec)
    assert not (tmp_path / "evil.txt").exists()


def test_archive_extraction_limits_are_enforced(tmp_path, spec):
    archive, _ = make_artifact(tmp_path, "large.zip", "x" * 128)
    spec.artifact.max_extract_bytes = 32
    with pytest.raises(RuntimeError, match="extracted-size"):
        runner._extract(archive, tmp_path / "out", spec)


async def test_download_limit_removes_partial_artifact(spec, monkeypatch):
    spec.artifact.allow_private_networks = True
    spec.artifact.max_download_bytes = 32
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64))
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    deploy = {
        "deploy_id": "d" * 32,
        "artifact_url": "https://example.com/a.zip",
    }

    with pytest.raises(RuntimeError, match="download limit"):
        await runner._step_download(spec, deploy, {})
    assert not (spec.releases_dir / ".incoming" / f"{deploy['deploy_id']}.artifact").exists()


async def test_unlisted_redirect_target_is_rejected(spec, monkeypatch):
    spec.artifact.allow_private_networks = True
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "https://evil.example/a.zip"})
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    deploy = {
        "deploy_id": "e" * 32,
        "artifact_url": "https://example.com/a.zip",
    }

    with pytest.raises(RuntimeError, match="redirect target"):
        await runner._step_download(spec, deploy, {})


@pytest.mark.parametrize(
    "redirect",
    [None, "https://release-assets.githubusercontent.com/file.zip", GITHUB_ASSET_URL + "4"],
)
@pytest.mark.parametrize("app_name", [None, "site"])
async def test_private_asset_download_drops_auth_on_every_redirect(
    spec, monkeypatch, redirect, app_name
):
    spec.artifact.allowed_url_prefix = "https://api.github.com/repos/acme/site/releases/assets/"
    spec.artifact.allowed_redirect_hosts = ["release-assets.githubusercontent.com"]
    spec.artifact.allow_private_networks = True
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "github_pat_test_secret")
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN_SITE", "github_pat_app_secret")
    expected_token = "github_pat_app_secret" if app_name else "github_pat_test_secret"
    requests = []

    def serve(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.headers["authorization"] == f"Bearer {expected_token}"
            assert request.headers["accept"] == "application/octet-stream"
            if redirect:
                return httpx.Response(302, headers={"location": redirect})
        else:
            assert "authorization" not in request.headers
        return httpx.Response(200, content=b"artifact")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(serve), **kwargs),
    )
    ctx = {}
    await runner._step_download(
        spec, {"app": app_name, "deploy_id": "f" * 32, "artifact_url": GITHUB_ASSET_URL}, ctx
    )
    assert ctx["artifact_path"].read_bytes() == b"artifact"
    assert len(requests) == (2 if redirect else 1)


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_private_asset_auth_failure_is_actionable_and_redacted(spec, monkeypatch, status):
    spec.artifact.allowed_url_prefix = "https://api.github.com/repos/acme/site/releases/assets/"
    spec.artifact.allow_private_networks = True
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "github_pat_test_secret")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status)), **kwargs
        ),
    )
    with pytest.raises(RuntimeError, match="Contents read") as error:
        await runner._step_download(
            spec, {"deploy_id": "f" * 32, "artifact_url": GITHUB_ASSET_URL}, {}
        )
    assert "github_pat_test_secret" not in str(error.value)
    assert not list((spec.releases_dir / ".incoming").iterdir())


async def test_private_asset_must_match_app_allowlist(spec, monkeypatch):
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "github_pat_test_secret")
    with pytest.raises(RuntimeError, match="not allowlisted"):
        await runner._step_download(
            spec, {"deploy_id": "f" * 32, "artifact_url": GITHUB_ASSET_URL}, {}
        )
    assert not spec.releases_dir.exists()


async def test_old_releases_pruned(tmp_path, spec, store, monkeypatch):
    spec.keep_previous = 1
    unrelated = spec.releases_dir / "manual-backup"
    unrelated.mkdir(parents=True)
    shas = [c * 40 for c in "cdef"]
    for i, sha in enumerate(shas):
        artifact, digest = make_artifact(tmp_path, f"r{i}.zip", f"r{i}")
        wire(monkeypatch, spec, artifact)
        await runner.run_deploy(store, "app-x", new_deploy(store, sha, digest))

    kept = {p.name for p in spec.releases_dir.iterdir() if not p.name.startswith(".")}
    assert "manual-backup" in kept
    assert {name.split("-", 1)[0] for name in kept if name != "manual-backup"} == set(shas[-2:])


@pytest.mark.parametrize(
    "keep,automatic,expected", [(1, True, 2), (0, True, 1), (2, True, 3), (0, False, 4)]
)
async def test_optional_retention(tmp_path, spec, store, monkeypatch, keep, automatic, expected):
    spec.keep_previous = keep
    spec.auto_cleanup = automatic
    for i, sha in enumerate("abcd"):
        artifact, digest = make_artifact(tmp_path, f"v{i}.zip", str(i))
        wire(monkeypatch, spec, artifact)
        await runner.run_deploy(store, "app-x", new_deploy(store, sha * 40, digest))
    assert len(runner.list_releases(spec)["releases"]) == expected
    assert (spec.current_link / "app.txt").read_text() == "3"
    if keep == 0 and automatic:
        assert not runner._previous_link(spec).is_symlink()


async def test_failed_deploy_preserves_current_when_retention_disabled(
    tmp_path, spec, store, monkeypatch
):
    spec.keep_previous = 0
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest))
    current = spec.current_link.resolve()
    wire(monkeypatch, spec, artifact, healthy=False)
    failed = new_deploy(store, SHA_V2, digest)
    await runner.run_deploy(store, "app-x", failed)
    assert store.get_deploy(failed)["status"] == "rolled_back"
    assert spec.current_link.resolve() == current
    assert current.exists()


@pytest.mark.parametrize("healthy", [True, False])
async def test_activate_retained_version_preserves_history(
    tmp_path, spec, store, monkeypatch, healthy
):
    paths = []
    for i, sha in enumerate("abc"):
        artifact, digest = make_artifact(tmp_path, f"v{i}.zip", str(i))
        wire(monkeypatch, spec, artifact)
        await runner.run_deploy(store, "app-x", new_deploy(store, sha * 40, digest))
        paths.append(spec.current_link.resolve())
    wire(monkeypatch, spec, artifact, healthy=healthy)
    activation = new_deploy(store, SHA_V1, digest)
    await runner.run_activation(store, "app-x", activation, paths[0].name)
    row = store.get_deploy(activation)
    assert row["status"] == ("succeeded" if healthy else "rolled_back")
    assert spec.current_link.resolve() == paths[0 if healthy else 2]
    assert runner._previous_link(spec).resolve() == paths[2 if healthy else 1]
    assert all(path.exists() for path in paths)
    assert not {"download", "unpack", "migrate"}.intersection(step["step"] for step in row["steps"])
    if healthy:
        spec.keep_previous = 1
        runner._prune_releases(spec)
        assert paths[0].exists() and paths[2].exists()
        assert not paths[1].exists()


def test_manual_cleanup_protects_links_and_active_version(spec, tmp_path):
    paths = [spec.releases_dir / (char * 40 + "-" + char * 32) for char in "abc"]
    for path in paths:
        path.mkdir(parents=True)
    runner._atomic_symlink(paths[2], spec.current_link)
    runner._atomic_symlink(paths[1], runner._previous_link(spec))
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = spec.releases_dir / ("d" * 40 + "-" + "d" * 32)
    linked.symlink_to(outside, target_is_directory=True)
    (spec.releases_dir / "broken").symlink_to(tmp_path / "missing")
    assert len(runner.list_releases(spec)["releases"]) == 3
    for name in (paths[2].name, paths[1].name, linked.name, "../outside", "previous"):
        with pytest.raises(ValueError):
            runner.remove_release(spec, name)
    runner.remove_release(spec, paths[0].name)
    assert not paths[0].exists()
    spec.keep_previous = 0
    runner.remove_release(spec, paths[1].name)
    assert not runner._previous_link(spec).is_symlink()
    assert paths[2].exists() and outside.exists()


def test_cutover_does_not_replace_unrelated_file(tmp_path):
    target = tmp_path / "release"
    target.mkdir()
    link = tmp_path / "current.previous"
    link.write_text("user data")
    with pytest.raises(RuntimeError, match="non-link"):
        runner._atomic_symlink(target, link)
    assert link.read_text() == "user data"


async def test_imported_site_can_be_restored_without_download(tmp_path, spec, store, monkeypatch):
    baseline = tmp_path / "imported-site"
    baseline.mkdir()
    (baseline / "index.html").write_text("original")
    runner._atomic_symlink(baseline, spec.current_link)
    artifact, digest = make_artifact(tmp_path, "v1.zip", "new")
    wire(monkeypatch, spec, artifact)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest))
    current = spec.current_link.resolve()
    imported = next(
        row for row in runner.list_releases(spec)["releases"] if row["name"] == "previous"
    )
    assert imported["protected"]
    activation = new_deploy(store, "0" * 40, "0" * 64)
    await runner.run_activation(store, "app-x", activation, "previous")
    assert store.get_deploy(activation)["status"] == "succeeded"
    assert spec.current_link.resolve() == baseline
    assert runner._previous_link(spec).resolve() == current
    assert (baseline / "index.html").read_text() == "original"


async def test_missing_activation_target_fails_without_cutover(spec, store, monkeypatch):
    monkeypatch.setattr(runner, "get_app_registry", lambda: {"app-x": spec})
    did = new_deploy(store, SHA_V1, "b" * 64)
    await runner.run_activation(store, "app-x", did, "previous")
    assert store.get_deploy(did)["status"] == "failed"
    assert not spec.current_link.exists()


async def test_same_sha_redeploy_failure_preserves_live_release(tmp_path, spec, store, monkeypatch):
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest))
    previous = spec.current_link.resolve()

    invalid = tmp_path / "invalid.artifact"
    invalid.write_bytes(b"not an archive")
    invalid_digest = hashlib.sha256(invalid.read_bytes()).hexdigest()
    wire(monkeypatch, spec, invalid)
    failed = new_deploy(store, SHA_V1, invalid_digest)
    await runner.run_deploy(store, "app-x", failed)

    assert store.get_deploy(failed)["status"] == "failed"
    assert spec.current_link.resolve() == previous
    assert (spec.current_link / "app.txt").read_text() == "v1"


async def test_cancelled_command_kills_child_process(tmp_path):
    marker = tmp_path / "orphaned"
    code = f"import pathlib,time;time.sleep(0.5);pathlib.Path({str(marker)!r}).write_text('alive')"
    task = asyncio.create_task(runner._run_cmd([sys.executable, "-c", code]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.6)
    assert not marker.exists()
