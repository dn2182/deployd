import errno
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import directory_layout as layout
from deployd.worker import runner

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"), reason="native directory exchange"
)


def test_baseline_activation_retention_and_manual_cleanup(spec):
    spec.keep_previous = 0
    layout.prepare(spec)
    first = spec.releases_dir / ("a" * 40 + "-" + "b" * 32)
    first.mkdir()
    (first / "index.html").write_text("deployed")
    layout.initialize(first)
    layout.cutover(spec, first, {})
    baseline = spec.releases_dir / "b4deployd"
    baseline.mkdir()
    (baseline / "index.html").write_text("original")
    layout.initialize(baseline)
    layout.prune(spec)
    assert baseline.is_dir()
    layout.cutover(spec, baseline, {})
    assert (spec.current_link / "index.html").read_text() == "original"
    row = layout.records(spec)["releases"][0]
    assert row["release_id"] == "b4deployd" and row["commit_sha"] is None
    layout.cutover(spec, first, {})
    layout.prune(spec)
    assert baseline.exists()
    layout.remove_release(spec, "b4deployd")
    assert not baseline.exists()


@pytest.fixture
def spec(tmp_path):
    return AppSpec.model_validate(
        {
            "release_layout": "directory",
            "releases_dir": tmp_path / "releases",
            "current_link": tmp_path / "releases/current",
            "artifact": {"allowed_url_prefix": "https://example.com/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/", "retries": 1},
        }
    )


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "state.sqlite3")
    instance.init()
    return instance


@pytest.fixture
def wired(spec, monkeypatch):
    monkeypatch.setattr(runner, "get_app_registry", lambda: {"site": spec})

    async def healthy(*args):
        return "healthy"

    monkeypatch.setitem(runner._STEP_FNS, "health", healthy)


async def deploy_version(tmp_path, spec, store, monkeypatch, char):
    artifact = tmp_path / f"{char}.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("index.html", char)

    async def download(spec_, deploy, ctx):
        destination = spec_.releases_dir / ".incoming" / f"{deploy['deploy_id']}.zip"
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(artifact, destination)
        ctx["artifact_path"] = destination

    monkeypatch.setitem(runner._STEP_FNS, "download", download)
    did = store.create_deploy(
        "site",
        char * 40,
        "https://example.com/app.zip",
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "test",
    )
    await runner.run_deploy(store, "site", did)
    return f"{char * 40}-{did}"


async def test_real_current_retention_and_fixed_external_link(
    tmp_path, spec, store, monkeypatch, wired
):
    external = tmp_path / "nginx-root"
    external.symlink_to(spec.current_link, target_is_directory=True)
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    assert not (spec.releases_dir / first).exists()
    assert spec.current_link.is_dir() and not spec.current_link.is_symlink()
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    third = await deploy_version(tmp_path, spec, store, monkeypatch, "c")
    assert external.readlink() == spec.current_link
    assert (external / "index.html").read_text() == "c"
    assert (spec.releases_dir / second / "index.html").read_text() == "b"
    assert not (spec.releases_dir / first).exists()
    assert not (spec.releases_dir / third).exists()
    rows = layout.records(spec)["releases"]
    assert len(rows) == 2 and rows[0]["name"] == "current"
    assert rows[0]["release_id"] == third and rows[1]["previous"]
    for row in rows:
        assert store.get_deploy(row["release_id"][41:])["status"] == "succeeded"


async def test_activate_old_directory_without_download_or_migration(
    tmp_path, spec, store, monkeypatch, wired
):
    spec.keep_previous = 3
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    third = await deploy_version(tmp_path, spec, store, monkeypatch, "c")
    did = store.create_deploy(
        "site", "a" * 40, "https://example.com/app.zip", "f" * 64, f"activate:{first}"
    )
    await runner.run_activation(store, "site", did, first)
    assert store.get_deploy(did)["status"] == "succeeded"
    assert (spec.current_link / "index.html").read_text() == "a"
    assert (spec.releases_dir / third / "index.html").read_text() == "c"
    assert not (spec.releases_dir / first).exists()
    assert layout.read_manifest(spec.current_link)["previous"] == third
    assert {step["step"] for step in store.get_deploy(did)["steps"]} == {
        "cutover",
        "restart",
        "health",
    }
    spec.keep_previous = 1
    runner._prune_releases(spec)
    assert len(layout.records(spec)["releases"]) == 2
    assert (spec.releases_dir / third).exists()


@pytest.mark.parametrize("activation", [False, True])
async def test_failed_health_restores_real_current(
    tmp_path, spec, store, monkeypatch, wired, activation
):
    spec.keep_previous = 1
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")

    async def unhealthy(spec_, deploy, ctx):
        if not ctx.get("rollback_verification"):
            raise RuntimeError("unhealthy")
        return "healthy"

    monkeypatch.setitem(runner._STEP_FNS, "health", unhealthy)
    if activation:
        did = store.create_deploy(
            "site", "a" * 40, "https://example.com/app.zip", "f" * 64, f"activate:{first}"
        )
        await runner.run_activation(store, "site", did, first)
    else:
        spec.keep_previous = 0
        name = await deploy_version(tmp_path, spec, store, monkeypatch, "c")
        did = name[41:]
    assert store.get_deploy(did)["status"] == "rolled_back"
    assert layout.read_manifest(spec.current_link)["name"] == second
    assert (spec.current_link / "index.html").read_text() == "b"
    assert (spec.releases_dir / first / "index.html").read_text() == "a"


async def test_failed_first_deploy_moves_files_aside(tmp_path, spec, store, monkeypatch, wired):
    async def unhealthy(*args):
        raise RuntimeError("unhealthy")

    monkeypatch.setitem(runner._STEP_FNS, "health", unhealthy)
    name = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    assert not spec.current_link.exists()
    assert (spec.releases_dir / name / "index.html").read_text() == "a"
    assert store.get_deploy(name[41:])["status"] == "failed"


async def test_zero_retention_and_manual_cleanup(tmp_path, spec, store, monkeypatch, wired):
    spec.auto_cleanup = False
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    with pytest.raises(ValueError, match="protected"):
        runner.remove_release(spec, first)
    with pytest.raises(ValueError):
        runner.remove_release(spec, "current")
    spec.keep_previous = 0
    runner.remove_release(spec, first)
    assert (spec.current_link / "index.html").read_text() == "b"
    spec.auto_cleanup = True
    await deploy_version(tmp_path, spec, store, monkeypatch, "c")
    assert not (spec.releases_dir / second).exists()
    assert len(layout.records(spec)["releases"]) == 1


async def test_process_interruption_recovers_archive_names(
    tmp_path, spec, store, monkeypatch, wired
):
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    old = layout.read_manifest(spec.current_link)
    second = "b" * 40 + "-" + "b" * 32
    target = spec.releases_dir / second
    target.mkdir()
    (target / "index.html").write_text("b")
    layout.initialize(target)
    data = layout.read_manifest(target)
    data["previous"] = old["name"]
    layout.write_manifest(target, data)
    layout.exchange(target, spec.current_link)
    with pytest.raises(ValueError, match="recovery"):
        layout.records(spec)
    layout.reconcile(spec)
    layout.reconcile(spec)
    assert (spec.releases_dir / first / "index.html").read_text() == "a"
    assert (spec.current_link / "index.html").read_text() == "b"
    assert not target.exists()


async def test_exchange_failure_preserves_live_files(tmp_path, spec, store, monkeypatch, wired):
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")

    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, "exchange unsupported")

    monkeypatch.setattr(layout, "prepare", lambda spec_: None)
    monkeypatch.setattr(layout, "exchange", unsupported)
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    assert store.get_deploy(second[41:])["status"] == "failed"
    assert layout.read_manifest(spec.current_link)["name"] == first
    assert (spec.current_link / "index.html").read_text() == "a"


def test_unmanaged_directories_links_and_metadata_are_rejected(tmp_path, spec):
    layout.prepare(spec)
    spec.current_link.mkdir()
    (spec.current_link / "index.html").write_text("user site")
    with pytest.raises(ValueError, match="unmanaged"):
        layout.reconcile(spec)
    with pytest.raises(ValueError):
        layout.local_release(spec, "../outside")
    reserved = spec.releases_dir / ("a" * 40 + "-" + "a" * 32)
    reserved.mkdir()
    (reserved / layout.MANIFEST).write_text(json.dumps({"name": "../../outside"}))
    with pytest.raises(ValueError, match="invalid"):
        layout.read_manifest(reserved)
    with pytest.raises(ValueError, match="reserved"):
        layout.initialize(reserved)
    link = spec.releases_dir / ("b" * 40 + "-" + "b" * 32)
    link.symlink_to(spec.current_link, target_is_directory=True)
    with pytest.raises(ValueError):
        layout.local_release(spec, link.name)
    assert (spec.current_link / "index.html").read_text() == "user site"


async def test_archive_rename_failure_after_exchange_rolls_back(
    tmp_path, spec, store, monkeypatch, wired
):
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    original_rename = Path.rename

    def fail_archive(path, target):
        if Path(target).name == first:
            raise OSError(errno.EACCES, "archive rename denied")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_archive)
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    assert store.get_deploy(second[41:])["status"] == "rolled_back"
    assert layout.read_manifest(spec.current_link)["name"] == first
    assert (spec.current_link / "index.html").read_text() == "a"
    assert layout.read_manifest(spec.releases_dir / second)["name"] == second


async def test_failed_rollback_exchange_keeps_both_versions(
    tmp_path, spec, store, monkeypatch, wired
):
    first = await deploy_version(tmp_path, spec, store, monkeypatch, "a")
    original_exchange = layout.exchange

    async def unhealthy(*args):
        def unavailable(*paths):
            raise OSError(errno.EACCES, "rollback exchange denied")

        monkeypatch.setattr(layout, "exchange", unavailable)
        raise RuntimeError("unhealthy")

    monkeypatch.setitem(runner._STEP_FNS, "health", unhealthy)
    second = await deploy_version(tmp_path, spec, store, monkeypatch, "b")
    assert store.get_deploy(second[41:])["status"] == "failed"
    assert layout.read_manifest(spec.current_link)["name"] == second
    assert (spec.releases_dir / first / "index.html").read_text() == "a"
    monkeypatch.setattr(layout, "exchange", original_exchange)
    layout.reconcile(spec)
    assert len(layout.records(spec)["releases"]) == 2
