import asyncio

from deployd.store.db import Store
from deployd.worker import queue, runner


def _store(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.init()
    return store


def _artifact(store, app="app"):
    return store.create_deploy(app, "a" * 40, "https://x/a.zip", "b" * 64, "test")


async def test_activation_and_deployment_share_app_queue(tmp_path, monkeypatch):
    store = _store(tmp_path)
    worker = queue.DeployQueue(store)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def deploy(*args):
        calls.append("deploy")
        started.set()
        await finish.wait()

    async def activate(*args):
        calls.append("activate")

    async def connect(*args):
        calls.append("connect")

    monkeypatch.setattr(runner, "run_deploy", deploy)
    monkeypatch.setattr(runner, "run_activation", activate)
    monkeypatch.setattr(queue, "run_connection", connect)
    one = _artifact(store)
    two = store.create_deploy(
        "app", "0" * 40, "local-release://previous", "0" * 64, "x", "activate"
    )
    three = store.create_deploy(
        "app", "0" * 40, "local-website://connect", "0" * 64, "x", "connect"
    )
    try:
        worker.enqueue("app", one)
        worker.enqueue_activation("app", two, "previous")
        worker.enqueue_connection("app", three)
        await asyncio.wait_for(started.wait(), 1)
        assert calls == ["deploy"]
        finish.set()
        await asyncio.wait_for(worker._queues["app"].join(), 1)
        assert calls == ["deploy", "activate", "connect"]
    finally:
        await worker.shutdown()


async def test_restart_does_not_replay_local_operations_as_download(tmp_path):
    store = _store(tmp_path)
    ids = [
        store.create_deploy("app", "a" * 40, "local-release://previous", "b" * 64, "x", "activate"),
        store.create_deploy("app", "0" * 40, "local-website://connect", "0" * 64, "x", "connect"),
        store.create_deploy("app", "0" * 40, "local-website://remove", "0" * 64, "x", "remove"),
    ]
    worker = queue.DeployQueue(store)
    assert worker.recover({"app"}) == 0
    assert all(store.get_deploy(did)["status"] == "failed" for did in ids)
    assert not worker._jobs


async def test_restart_resumes_queued_artifact_deploys(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _artifact(store)
    ran = asyncio.Event()

    async def deploy(*args):
        ran.set()

    monkeypatch.setattr(runner, "run_deploy", deploy)
    worker = queue.DeployQueue(store)
    try:
        assert worker.recover({"app"}) == 1
        await asyncio.wait_for(ran.wait(), 1)
    finally:
        await worker.shutdown()


async def test_removal_lock_lasts_until_worker_finishes(tmp_path, monkeypatch):
    store = _store(tmp_path)
    worker = queue.DeployQueue(store)
    finish = asyncio.Event()

    async def remove(*args):
        await finish.wait()

    monkeypatch.setattr(queue, "run_removal", remove)
    did = store.create_deploy("site", "0" * 40, "local-website://remove", "0" * 64, "x", "remove")
    try:
        worker.enqueue_removal("site", did)
        assert worker.is_removing("site")
        assert not worker.is_removing("other")
        finish.set()
        await asyncio.wait_for(worker._queues["site"].join(), 1)
        assert not worker.is_removing("site")
    finally:
        await worker.shutdown()


async def test_cancelled_and_superseded_rows_are_skipped(tmp_path, monkeypatch):
    store = _store(tmp_path)
    worker = queue.DeployQueue(store)
    ran = []

    async def deploy(store_, app, deploy_id):
        ran.append(deploy_id)

    monkeypatch.setattr(runner, "run_deploy", deploy)
    cancelled, live = _artifact(store), _artifact(store)
    assert store.cancel_deploy(cancelled, "tester")
    try:
        worker.enqueue("app", cancelled)
        worker.enqueue("app", live)
        await asyncio.wait_for(worker._queues["app"].join(), 1)
    finally:
        await worker.shutdown()
    assert ran == [live]
    assert store.get_deploy(cancelled)["status"] == "cancelled"


async def test_dead_worker_is_respawned_and_queue_keeps_moving(tmp_path, monkeypatch):
    store = _store(tmp_path)
    worker = queue.DeployQueue(store)
    ran = []

    async def deploy(store_, app, deploy_id):
        ran.append(deploy_id)

    monkeypatch.setattr(runner, "run_deploy", deploy)
    monkeypatch.setattr(worker, "_record_crash", lambda deploy_id: 1 / 0)
    first, second = _artifact(store), _artifact(store)
    try:
        worker.enqueue("app", first)
        await asyncio.wait_for(worker._queues["app"].join(), 1)

        async def boom(*args):
            raise RuntimeError("worker crash")

        monkeypatch.setattr(runner, "run_deploy", boom)
        crash = _artifact(store)
        worker.enqueue("app", crash)
        await asyncio.wait_for(worker._queues["app"].join(), 1)
        await asyncio.sleep(0)
        assert worker.healthy()
        monkeypatch.setattr(runner, "run_deploy", deploy)
        worker.enqueue("app", second)
        await asyncio.wait_for(worker._queues["app"].join(), 1)
    finally:
        await worker.shutdown()
    assert ran == [first, second]


async def test_shutdown_waits_for_protected_work(tmp_path, monkeypatch):
    store = _store(tmp_path)
    worker = queue.DeployQueue(store, drain_timeout=2)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def cutover():
        await release.wait()
        finished.set()

    async def deploy(store_, app, deploy_id):
        await runner._protected(cutover())

    monkeypatch.setattr(runner, "run_deploy", deploy)
    worker.enqueue("app", _artifact(store))
    await asyncio.sleep(0.05)
    stopper = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0.05)
    assert not stopper.done()
    release.set()
    await asyncio.wait_for(stopper, 1)
    assert finished.is_set()
