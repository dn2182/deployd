import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from deployd import main
from deployd.api import admin
from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import directory_layout, runner
from deployd.worker.queue import DeployQueue


@pytest.fixture
def release(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite3")
    store.init()
    root = tmp_path / "releases"
    root.mkdir()
    spec = AppSpec(
        releases_dir=root,
        current_link=root / "current",
        release_layout="directory",
        artifact={"allowed_url_prefix": "https://example.com/"},
        restart={"command": ["true"]},
    )
    did = store.create_deploy("site", "a" * 40, "https://example.com/a", "b" * 64, "test")
    store.set_status(did, "succeeded", finished=True)
    path = root / f"{'a' * 40}-{did}"
    path.mkdir()
    directory_layout.initialize(path)
    monkeypatch.setattr(admin, "get_app_registry", lambda: {"site": spec})
    monkeypatch.setattr(runner, "get_app_registry", lambda: {"site": spec})
    worker = DeployQueue(store)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(store=store, queue=worker)),
        headers={},
    )
    return store, spec, path, worker, request


async def test_cleanup_blocks_activation_and_serializes_later_deploys(release, monkeypatch):
    store, spec, path, worker, request = release
    started, finish = threading.Event(), threading.Event()
    calls = []
    remove = runner.remove_release

    def blocked_remove(*args):
        started.set()
        assert finish.wait(5)
        remove(*args)
        calls.append("cleanup")

    async def deploy(_store, app, did):
        calls.append(app)
        _store.set_status(did, "succeeded", finished=True)

    monkeypatch.setattr(runner, "remove_release", blocked_remove)
    monkeypatch.setattr(runner, "run_deploy", deploy)
    selection = admin.ReleaseSelection(release=path.name)
    result = await admin.cleanup_app_release(request, "site", selection)
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert store.get_deploy(result["deploy_id"])["kind"] == "cleanup"
        assert (await admin.list_app_releases(request, "site"))["busy"]
        for operation in (admin.activate_app_release, admin.cleanup_app_release):
            with pytest.raises(HTTPException) as error:
                await operation(request, "site", selection)
            assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            admin._validate_app_update(request, "site", spec)
        assert error.value.status_code == 409
        for app in ("site", "other"):
            did = store.create_deploy(app, "c" * 40, "https://example.com/b", "d" * 64, "CI")
            worker.enqueue(app, did)
        await asyncio.wait_for(worker._queues["other"].join(), 2)
        assert calls == ["other"]
        finish.set()
        await asyncio.wait_for(worker._queues["site"].join(), 2)
        assert calls == ["other", "cleanup", "site"]
        assert not path.exists()
        assert store.get_deploy(result["deploy_id"])["status"] == "succeeded"
    finally:
        finish.set()
        await worker.shutdown()


async def test_shutdown_drains_in_progress_cleanup(release, monkeypatch):
    store, _, path, worker, request = release
    started, finish = threading.Event(), threading.Event()
    remove = runner.remove_release

    def blocked_remove(*args):
        started.set()
        assert finish.wait(5)
        remove(*args)

    monkeypatch.setattr(runner, "remove_release", blocked_remove)
    result = await admin.cleanup_app_release(
        request, "site", admin.ReleaseSelection(release=path.name)
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        shutdown = asyncio.create_task(worker.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert store.has_active_deploys("site")
        finish.set()
        await asyncio.wait_for(shutdown, 2)
        assert not path.exists()
        assert store.get_deploy(result["deploy_id"])["status"] == "succeeded"
    finally:
        finish.set()
        await worker.shutdown()


async def test_cleanup_rechecks_protection_in_worker(release):
    store, spec, path, worker, request = release
    result = await admin.cleanup_app_release(
        request, "site", admin.ReleaseSelection(release=path.name)
    )
    current = spec.current_link
    current.mkdir()
    directory_layout.write_manifest(
        current,
        {
            "name": f"{'c' * 40}-{'d' * 32}",
            "previous": path.name,
            "created_at": 1,
        },
    )
    try:
        await asyncio.wait_for(worker._queues["site"].join(), 2)
        assert store.get_deploy(result["deploy_id"])["status"] == "failed"
        assert path.exists()
    finally:
        await worker.shutdown()


async def test_maintenance_preserves_old_releases_and_activation_records(release, monkeypatch):
    store, _, path, worker, request = release
    with store._conn() as conn:
        conn.execute("UPDATE deploys SET created_at='2020-01-01', finished_at='2020-01-01'")
    sleeps = 0

    async def one_cycle(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    with monkeypatch.context() as context:
        context.setattr(main.asyncio, "sleep", one_cycle)
        with pytest.raises(asyncio.CancelledError):
            await main._maintenance(store)
    rows = await admin.list_app_releases(request, "site")
    assert rows["releases"][0]["can_activate"]
    assert path.exists()
    assert store.get_deploy(path.name[41:])["status"] == "succeeded"
    queued = []
    monkeypatch.setattr(worker, "enqueue_activation", lambda *args: queued.append(args))
    result = await admin.activate_app_release(
        request, "site", admin.ReleaseSelection(release=path.name)
    )
    assert result["status"] == "queued" and queued[0][2] == path.name
