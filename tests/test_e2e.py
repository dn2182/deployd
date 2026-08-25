import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from deployd import config
from deployd.main import create_app
from deployd.security import compute_signature
from deployd.worker import runner
from test_runner import make_artifact

SECRET = "e2e-secret"
SHA = "f" * 40


@pytest.fixture
def env(tmp_path, monkeypatch):
    apps_yaml = tmp_path / "apps.yaml"
    apps_yaml.write_text(
        f"""
apps:
  app-x:
    releases_dir: {tmp_path}/releases
    current_link: {tmp_path}/current
    artifact:
      allowed_url_prefix: "https://example.com/"
    restart:
      command: ["true"]
    health:
      url: "http://127.0.0.1:1/healthz"
      retries: 1
      interval_seconds: 0
"""
    )
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(apps_yaml))
    monkeypatch.setenv("DEPLOYD_SECRET_APP_X", SECRET)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


def signed_headers(body: bytes, *, secret: str = SECRET, nonce: str = "e2e-nonce-1") -> dict:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Deploy-Timestamp": ts,
        "X-Deploy-Nonce": nonce,
        "X-Deploy-Signature": "sha256=" + compute_signature(secret, ts, body),
    }


def deploy_body(digest: str) -> bytes:
    return json.dumps(
        {
            "app": "app-x",
            "commit_sha": SHA,
            "artifact_url": "https://example.com/app.zip",
            "artifact_sha256": digest,
            "triggered_by": "e2e-test",
        }
    ).encode()


def test_signed_request_deploys_end_to_end(env, monkeypatch):
    artifact, digest = make_artifact(env, "app.zip", "e2e")

    async def fake_download(spec, deploy, ctx):
        dest = spec.releases_dir / ".incoming" / f"{deploy['commit_sha']}.artifact"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(artifact, dest)
        ctx["artifact_path"] = dest
        return "copied"

    async def fake_health(spec, deploy, ctx):
        return "healthy"

    monkeypatch.setitem(runner._STEP_FNS, "download", fake_download)
    monkeypatch.setitem(runner._STEP_FNS, "health", fake_health)

    with TestClient(create_app()) as client:
        body = deploy_body(digest)
        resp = client.post("/deploys", content=body, headers=signed_headers(body))
        assert resp.status_code == 202
        deploy_id = resp.json()["deploy_id"]

        status = None
        for _ in range(200):
            status = client.get(f"/deploys/{deploy_id}").json()
            if status["status"] in ("succeeded", "failed", "rolled_back"):
                break
            time.sleep(0.05)

        assert status["status"] == "succeeded", status["steps"]
        assert (env / "current" / "app.txt").read_text() == "e2e"


def test_bad_signature_rejected(env):
    with TestClient(create_app()) as client:
        body = deploy_body("0" * 64)
        headers = signed_headers(body, secret="wrong-secret")
        assert client.post("/deploys", content=body, headers=headers).status_code == 401


def test_replayed_nonce_rejected(env, monkeypatch):
    async def boom(spec, deploy, ctx):
        raise RuntimeError("stop early")

    monkeypatch.setitem(runner._STEP_FNS, "download", boom)
    with TestClient(create_app()) as client:
        body = deploy_body("0" * 64)
        headers = signed_headers(body, nonce="replay-me")
        assert client.post("/deploys", content=body, headers=headers).status_code == 202
        assert client.post("/deploys", content=body, headers=headers).status_code == 401
