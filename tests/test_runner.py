import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import pytest

from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import runner

SHA_V1 = "a" * 40
SHA_V2 = "b" * 40


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
        if not healthy:
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
    assert spec.current_link.resolve() == (spec.releases_dir / SHA_V1).resolve()
    assert (spec.current_link / "app.txt").read_text() == "v1"
    succeeded = [s["step"] for s in row["steps"] if s["status"] == "succeeded"]
    assert succeeded == runner.STEPS


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

    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2, healthy=False)
    did2 = new_deploy(store, SHA_V2, digest2)
    await runner.run_deploy(store, "app-x", did2)

    row = store.get_deploy(did2)
    assert row["status"] == "rolled_back"
    assert spec.current_link.resolve() == (spec.releases_dir / SHA_V1).resolve()
    assert (spec.current_link / "app.txt").read_text() == "v1"


async def test_health_failure_without_previous_release_fails(tmp_path, spec, store, monkeypatch):
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact, healthy=False)
    did = new_deploy(store, SHA_V1, digest)

    await runner.run_deploy(store, "app-x", did)

    row = store.get_deploy(did)
    assert row["status"] == "failed"
    rollback = [s for s in row["steps"] if s["step"] == "rollback"]
    assert rollback[0]["status"] == "skipped"


async def test_migration_failure_halts_without_touching_current(
    tmp_path, spec, store, monkeypatch
):
    artifact1, digest1 = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact1)
    await runner.run_deploy(store, "app-x", new_deploy(store, SHA_V1, digest1))

    spec.migrate.command = ["false"]
    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2)
    did2 = new_deploy(store, SHA_V2, digest2)
    await runner.run_deploy(store, "app-x", did2)

    row = store.get_deploy(did2)
    assert row["status"] == "failed"
    assert spec.current_link.resolve() == (spec.releases_dir / SHA_V1).resolve()


def test_zip_path_traversal_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "pwned")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buf.getvalue())

    with pytest.raises(RuntimeError, match="escapes release dir"):
        runner._extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


async def test_old_releases_pruned(tmp_path, spec, store, monkeypatch):
    spec.keep_releases = 2
    shas = [c * 40 for c in "cdef"]
    for i, sha in enumerate(shas):
        artifact, digest = make_artifact(tmp_path, f"r{i}.zip", f"r{i}")
        wire(monkeypatch, spec, artifact)
        await runner.run_deploy(store, "app-x", new_deploy(store, sha, digest))

    kept = {p.name for p in spec.releases_dir.iterdir() if not p.name.startswith(".")}
    assert kept == set(shas[-2:])
