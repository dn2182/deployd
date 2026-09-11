import asyncio

from deployd.store.db import Store
from deployd.worker import queue


async def test_activation_and_deployment_share_app_queue(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite3")
    store.init()
    worker = queue.DeployQueue(store)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def deploy(*args):
        calls.append("deploy")
        started.set()
        await finish.wait()

    async def activate(*args):
        calls.append("activate")

    monkeypatch.setattr(queue, "run_deploy", deploy)
    monkeypatch.setattr(queue, "run_activation", activate)
    try:
        worker.enqueue("app", "one")
        worker.enqueue_activation("app", "two", "previous")
        await asyncio.wait_for(started.wait(), 1)
        assert calls == ["deploy"]
        finish.set()
        await asyncio.wait_for(worker._queues["app"].join(), 1)
        assert calls == ["deploy", "activate"]
    finally:
        await worker.shutdown()


async def test_restart_does_not_replay_local_activation_as_download(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.init()
    did = store.create_deploy(
        "app", "a" * 40, "local-release://previous", "b" * 64, "activate:previous"
    )
    worker = queue.DeployQueue(store)
    assert worker.recover({"app"}) == 0
    assert store.get_deploy(did)["status"] == "failed"
    assert not worker._pending_ids
