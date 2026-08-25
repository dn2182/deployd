from deployd.store.db import Store


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
    assert not s.nonce_seen("n1")
    s.record_nonce("n1")
    assert s.nonce_seen("n1")
