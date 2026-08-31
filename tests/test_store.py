from concurrent.futures import ThreadPoolExecutor

import pytest

from deployd.store.db import InstanceLock, Store


def _store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    s.init()
    return s


def test_deploy_lifecycle(tmp_path):
    s = _store(tmp_path)
    did = s.create_deploy("app-a", "a" * 40, "https://x/artifact.zip", "b" * 64, "test")
    s.set_status(did, "running")
    s.add_step(did, "download", "succeeded")
    s.set_status(did, "succeeded", finished=True)

    row = s.get_deploy(did)
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None
    assert [st["step"] for st in row["steps"]] == ["download"]


def test_unknown_deploy_returns_none(tmp_path):
    assert _store(tmp_path).get_deploy("nope") is None


def test_nonce_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.consume_nonce("n1") is True
    assert s.consume_nonce("n1") is False


def test_nonce_consumption_is_atomic(tmp_path):
    s = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: s.consume_nonce("same"), range(32)))
    assert results.count(True) == 1
    assert results.count(False) == 31


def test_identical_deploy_request_is_idempotent(tmp_path):
    s = _store(tmp_path)
    args = ("n1", "request-hash", "app-a", "a" * 40, "https://x/a.zip", "b" * 64, "test")
    first = s.create_deploy_once(*args)
    second = s.create_deploy_once(*args)
    assert first[1] is True
    assert second == (first[0], False)
    assert s.create_deploy_once("n1", "different", *args[2:]) is None


def test_recovery_fails_running_and_returns_queued(tmp_path):
    s = _store(tmp_path)
    running = s.create_deploy("app-a", "a" * 40, "https://x/a.zip", "b" * 64, "test")
    queued = s.create_deploy("app-b", "c" * 40, "https://x/b.zip", "d" * 64, "test")
    s.set_status(running, "running")

    assert s.recover_after_restart() == [("app-b", queued)]
    interrupted = s.get_deploy(running)
    assert interrupted["status"] == "failed"
    assert interrupted["steps"][-1]["step"] == "recovery"


def test_instance_lock_rejects_second_owner(tmp_path):
    first = InstanceLock(tmp_path / "state.sqlite3")
    second = InstanceLock(tmp_path / "state.sqlite3")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another deployd process"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
