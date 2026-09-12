import asyncio
import io
import os
import stat
import sys
import zipfile

import httpx
import pytest
from test_runner import SHA_V1, make_artifact, new_deploy, wire

from deployd import notify
from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import runner

pytestmark = pytest.mark.skipif(os.name == "nt", reason="posix process semantics")


def _spec(tmp_path, **overrides):
    data = {
        "releases_dir": str(tmp_path / "releases"),
        "current_link": str(tmp_path / "current"),
        "artifact": {"allowed_url_prefix": "https://example.com/"},
        "restart": {"command": ["true"]},
        "health": {"url": "http://127.0.0.1:1/healthz", "retries": 1, "interval_seconds": 0},
    }
    data.update(overrides)
    return AppSpec.model_validate(data)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    s.init()
    return s


async def test_commands_do_not_inherit_service_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", "super-secret-admin-token-value-123456")
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("DEPLOYD_SECRET_APP_X", "s" * 32)
    monkeypatch.setenv("HOME", str(tmp_path))
    code = "import os,json;print(json.dumps(sorted(k for k in os.environ if k.startswith('DEPLOYD_'))));print(os.environ['HOME'])"
    output = await runner._run_cmd(
        [sys.executable, "-c", code], env={"DEPLOYD_APP": "app-x", "DEPLOYD_COMMIT_SHA": "abc"}
    )
    lines = output.splitlines()
    assert lines[0] == '["DEPLOYD_APP", "DEPLOYD_COMMIT_SHA"]'
    assert lines[1] == str(tmp_path)


async def test_command_timeout_is_per_call():
    code = "import time; time.sleep(5)"
    with pytest.raises(RuntimeError, match="timed out after 0.2s"):
        await runner._run_cmd([sys.executable, "-c", code], timeout=0.2)


async def test_restart_that_leaves_a_daemon_does_not_hang_or_get_killed(tmp_path):
    marker = tmp_path / "daemon-lived"
    daemon = (
        f"import pathlib,time;time.sleep(1.5);pathlib.Path({str(marker)!r}).write_text('alive')"
    )
    code = (
        f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{daemon!r}]);print('started')"
    )
    started = asyncio.get_running_loop().time()
    output = await runner._run_cmd([sys.executable, "-c", code], timeout=10)
    assert asyncio.get_running_loop().time() - started < 3
    assert output.startswith("started")
    assert "background process kept stdout open" in output
    await asyncio.sleep(2)
    assert marker.read_text() == "alive"


def test_zip_extraction_keeps_executable_bits(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("bin/run.sh")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, "#!/bin/sh\n")
        z.writestr("plain.txt", "x")
    archive = tmp_path / "a.zip"
    archive.write_bytes(buf.getvalue())
    runner._extract(archive, tmp_path / "out", _spec(tmp_path))
    assert (tmp_path / "out/bin/run.sh").stat().st_mode & 0o111
    assert (tmp_path / "out/plain.txt").is_file()


async def test_hooks_run_around_cutover_and_see_deploy_context(tmp_path, store, monkeypatch):
    before_log = tmp_path / "before.txt"
    after_log = tmp_path / "after.txt"
    spec = _spec(
        tmp_path,
        hooks={
            "before_cutover": [
                sys.executable,
                "-c",
                f"import os,pathlib;pathlib.Path({str(before_log)!r}).write_text(os.environ['DEPLOYD_RELEASE_DIR']+'|'+os.getcwd()+'|'+os.environ['DEPLOYD_COMMIT_SHA'])",
            ],
            "after_health": [
                sys.executable,
                "-c",
                f"import os,pathlib;pathlib.Path({str(after_log)!r}).write_text(os.environ['DEPLOYD_CURRENT'])",
            ],
        },
    )
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    did = new_deploy(store, SHA_V1, digest)
    await runner.run_deploy(store, "app-x", did)
    row = store.get_deploy(did)
    assert row["status"] == "succeeded", row["steps"]
    assert [s["step"] for s in row["steps"]] == [
        "download",
        "verify",
        "unpack",
        "migrate",
        "before_cutover",
        "cutover",
        "restart",
        "health",
        "after_health",
    ]
    release_dir, cwd, sha = before_log.read_text().split("|")
    assert release_dir == cwd and sha == SHA_V1
    assert release_dir.endswith(f"{SHA_V1}-{did}")
    assert after_log.read_text() == str(spec.current_link)


async def test_failed_after_health_hook_rolls_back(tmp_path, store, monkeypatch):
    spec = _spec(tmp_path, hooks={"after_health": ["false"]})
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    first = new_deploy(store, SHA_V1, digest)
    spec_ok = _spec(tmp_path)
    wire(monkeypatch, spec_ok, artifact)
    await runner.run_deploy(store, "app-x", first)
    assert store.get_deploy(first)["status"] == "succeeded"
    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2)
    second = new_deploy(store, "b" * 40, digest2)
    await runner.run_deploy(store, "app-x", second)
    assert store.get_deploy(second)["status"] == "rolled_back"
    assert (spec.current_link / "app.txt").read_text() == "v1"


async def test_health_expectations_reject_stale_process(tmp_path):
    spec = _spec(
        tmp_path,
        health={
            "url": "http://app.local/healthz",
            "retries": 2,
            "interval_seconds": 0,
            "expect_body": "release {commit_sha}",
            "expect_header": "X-Release: {commit_sha}",
        },
    )
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text="release old", headers={"X-Release": "old"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client_factory(**kwargs):
        return original(transport=transport, **kwargs)

    runner.httpx.AsyncClient = client_factory
    try:
        with pytest.raises(RuntimeError, match="response body lacks"):
            await runner._step_health(spec, {"commit_sha": "new"}, {})
        assert len(seen) == 2
        assert (
            await runner._step_health(spec, {"commit_sha": "old"}, {})
            == "healthy after 1 attempt(s)"
        )
        assert (
            await runner._step_health(spec, {}, {"rollback_verification": True})
            == "healthy after 1 attempt(s)"
        )
    finally:
        runner.httpx.AsyncClient = original


async def test_transient_download_errors_are_retried(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    attempts = []

    def handler(request):
        attempts.append(request.url)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"payload", headers={"content-length": "7"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    runner.httpx.AsyncClient = lambda **kw: original(transport=transport, **kw)
    monkeypatch.setattr(runner, "DOWNLOAD_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(runner, "_validate_network_target", _noop)
    ctx = {}
    try:
        out = await runner._step_download(
            spec, {"deploy_id": "d1", "artifact_url": "https://example.com/a.zip", "app": "x"}, ctx
        )
    finally:
        runner.httpx.AsyncClient = original
    assert out == "7 bytes" and len(attempts) == 3
    assert ctx["artifact_sha256"] == __import__("hashlib").sha256(b"payload").hexdigest()


async def test_permanent_download_errors_are_not_retried(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404)

    original = httpx.AsyncClient
    runner.httpx.AsyncClient = lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(runner, "_validate_network_target", _noop)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await runner._step_download(
                spec, {"deploy_id": "d1", "artifact_url": "https://example.com/a.zip"}, {}
            )
    finally:
        runner.httpx.AsyncClient = original
    assert attempts == [1]


async def test_download_refuses_when_disk_is_short(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    original = httpx.AsyncClient
    runner.httpx.AsyncClient = lambda **kw: original(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"x", headers={"content-length": "1"})
        ),
        **kw,
    )
    monkeypatch.setattr(runner, "_validate_network_target", _noop)
    monkeypatch.setattr(runner, "DISK_HEADROOM_BYTES", 1 << 60)
    try:
        with pytest.raises(RuntimeError, match="insufficient free disk space"):
            await runner._step_download(
                spec, {"deploy_id": "d1", "artifact_url": "https://example.com/a.zip"}, {}
            )
    finally:
        runner.httpx.AsyncClient = original
    assert not list((spec.releases_dir / ".incoming").iterdir())


async def _noop(*args, **kwargs):
    return None


def test_sweep_incoming_removes_leftovers(tmp_path):
    spec = _spec(tmp_path)
    incoming = spec.releases_dir / ".incoming"
    (incoming / "staging").mkdir(parents=True)
    (incoming / "staging/file").write_text("x")
    (incoming / "old.artifact").write_bytes(b"x")
    assert runner.sweep_incoming(spec) == 2
    assert not list(incoming.iterdir())
    assert runner.sweep_incoming(_spec(tmp_path / "missing")) == 0


async def test_interrupted_staged_deploy_returns_to_queue(tmp_path, store, monkeypatch):
    spec = _spec(tmp_path)
    monkeypatch.setattr(runner, "get_app_registry", lambda: {"app-x": spec})
    entered = asyncio.Event()

    async def slow_download(spec_, deploy, ctx):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setitem(runner._STEP_FNS, "download", slow_download)
    did = new_deploy(store, SHA_V1, "0" * 64)
    task = asyncio.create_task(runner.run_deploy(store, "app-x", did))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = store.get_deploy(did)
    assert row["status"] == "queued"
    assert row["steps"][-1]["step"] == "interrupted"


async def test_cutover_phase_survives_cancellation(tmp_path, store, monkeypatch):
    spec = _spec(tmp_path)
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    gate = asyncio.Event()

    async def slow_restart(spec_, deploy, ctx):
        await gate.wait()
        return "restarted"

    monkeypatch.setitem(runner._STEP_FNS, "restart", slow_restart)
    did = new_deploy(store, SHA_V1, digest)
    task = asyncio.create_task(runner.run_deploy(store, "app-x", did))
    for _ in range(200):
        await asyncio.sleep(0.01)
        steps = {s["step"]: s["status"] for s in store.get_deploy(did)["steps"]}
        if steps.get("restart") == "running":
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get_deploy(did)["status"] == "running"
    gate.set()
    assert await runner.drain(2) == 0
    assert store.get_deploy(did)["status"] == "succeeded"
    assert (spec.current_link / "app.txt").read_text() == "v1"


async def test_failed_deploy_sends_notification(tmp_path, store, monkeypatch):
    spec = _spec(
        tmp_path,
        notify={"url": "https://hooks.example.com/x", "events": ["failed"], "format": "generic"},
    )
    posted = []

    async def fake_send(spec_, deploy, status, failed_step=None):
        posted.append((status, failed_step))

    monkeypatch.setattr(notify, "send", fake_send)
    artifact, _ = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    did = new_deploy(store, SHA_V1, "0" * 64)
    await runner.run_deploy(store, "app-x", did)
    assert store.get_deploy(did)["status"] == "failed"
    assert posted == [("failed", "verify")]


async def test_activation_never_deletes_the_release_it_uses(tmp_path, store, monkeypatch):
    spec = _spec(tmp_path)
    artifact, digest = make_artifact(tmp_path, "v1.zip", "v1")
    wire(monkeypatch, spec, artifact)
    first = new_deploy(store, SHA_V1, digest)
    await runner.run_deploy(store, "app-x", first)
    artifact2, digest2 = make_artifact(tmp_path, "v2.zip", "v2")
    wire(monkeypatch, spec, artifact2)
    second = new_deploy(store, "b" * 40, digest2)
    await runner.run_deploy(store, "app-x", second)
    retained = spec.releases_dir / f"{SHA_V1}-{first}"
    assert retained.is_dir()
    wire(monkeypatch, spec, artifact2, healthy=False)
    activation = store.create_deploy("app-x", SHA_V1, "x", "y", "activate:x", "activate")
    await runner.run_activation(store, "app-x", activation, retained.name)
    assert store.get_deploy(activation)["status"] == "rolled_back"
    assert retained.is_dir()
    assert (spec.current_link / "app.txt").read_text() == "v2"
