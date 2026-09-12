import json
import time

import pytest
from fastapi.testclient import TestClient

from deployd import config
from deployd.main import create_app
from deployd.security import compute_signature
from deployd.worker import runner

ADMIN_TOKEN = "a" * 32
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}
SECRET = "e" * 32


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
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("DEPLOYD_SECRET_APP_X", SECRET)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


def _signed(body: bytes, nonce: str, secret: str = SECRET) -> dict:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Deploy-Timestamp": ts,
        "X-Deploy-Nonce": nonce,
        "X-Deploy-Signature": "sha256=" + compute_signature(secret, ts, nonce, body),
    }


def _body(app="app-x", sha="f" * 40) -> bytes:
    return json.dumps(
        {
            "app": app,
            "commit_sha": sha,
            "artifact_url": "https://example.com/app.zip",
            "artifact_sha256": "0" * 64,
            "triggered_by": "ci",
        }
    ).encode()


@pytest.fixture
def hold_worker(monkeypatch):
    """Keep every deploy parked in the download step so queue state is observable."""
    import asyncio

    async def park(spec, deploy, ctx):
        await asyncio.sleep(30)

    monkeypatch.setitem(runner._STEP_FNS, "download", park)


def test_unknown_app_and_missing_secret_answer_401(env, monkeypatch):
    with TestClient(create_app()) as client:
        body = _body(app="ghost")
        assert client.post("/deploys", content=body, headers=_signed(body, "n1")).status_code == 401
        monkeypatch.delenv("DEPLOYD_SECRET_APP_X")
        body = _body()
        response = client.post("/deploys", content=body, headers=_signed(body, "n2"))
        assert response.status_code == 401
        assert response.json()["detail"] == "unauthorized"


def test_frozen_app_rejects_ci_deploys_with_423(env, hold_worker):
    with TestClient(create_app()) as client:
        assert client.post(
            "/admin/apps/app-x/freeze", headers=ADMIN, json={"frozen": True}
        ).json() == {"app": "app-x", "frozen": True}
        body = _body()
        assert client.post("/deploys", content=body, headers=_signed(body, "n1")).status_code == 423
        assert client.get("/admin/apps", headers=ADMIN).json()["app-x"]["frozen"] is True
        assert client.get("/admin/apps/app-x/status", headers=ADMIN).json()["frozen"] is True
        client.post("/admin/apps/app-x/freeze", headers=ADMIN, json={"frozen": False})
        assert client.post("/deploys", content=body, headers=_signed(body, "n2")).status_code == 202
        actions = [row["action"] for row in client.get("/admin/audit", headers=ADMIN).json()]
        assert actions == ["app.unfreeze", "app.freeze"]
        assert (
            client.post("/admin/apps/nope/freeze", headers=ADMIN, json={"frozen": True}).status_code
            == 404
        )


def test_newer_push_supersedes_queued_deploys(env, hold_worker):
    with TestClient(create_app()) as client:
        first = client.post(
            "/deploys", content=_body(sha="1" * 40), headers=_signed(_body(sha="1" * 40), "a")
        )
        second = client.post(
            "/deploys", content=_body(sha="2" * 40), headers=_signed(_body(sha="2" * 40), "b")
        )
        third = client.post(
            "/deploys", content=_body(sha="3" * 40), headers=_signed(_body(sha="3" * 40), "c")
        )
        assert first.status_code == second.status_code == third.status_code == 202
        store = client.app.state.store
        assert store.get_deploy(first.json()["deploy_id"])["status"] == "running"
        assert store.get_deploy(second.json()["deploy_id"])["status"] == "superseded"
        assert store.get_deploy(third.json()["deploy_id"])["status"] == "queued"
        detail = client.get(f"/deploys/{second.json()['deploy_id']}").json()
        assert detail["status"] == "superseded" and detail["kind"] == "artifact"


def test_queued_deploy_can_be_cancelled(env, hold_worker):
    with TestClient(create_app()) as client:
        running = client.post("/deploys", content=_body(), headers=_signed(_body(), "a")).json()
        queued = client.post(
            "/deploys", content=_body(sha="2" * 40), headers=_signed(_body(sha="2" * 40), "b")
        ).json()
        did = queued["deploy_id"]
        assert client.post(
            f"/admin/deploys/{did}/cancel", headers={**ADMIN, "X-Remote-User": "dan"}
        ).json() == {
            "deploy_id": did,
            "status": "cancelled",
        }
        assert client.post(f"/admin/deploys/{did}/cancel", headers=ADMIN).status_code == 409
        assert (
            client.post(f"/admin/deploys/{running['deploy_id']}/cancel", headers=ADMIN).status_code
            == 409
        )
        assert client.post("/admin/deploys/nope/cancel", headers=ADMIN).status_code == 404
        audit = client.get("/admin/audit", headers=ADMIN).json()[0]
        assert audit["actor"] == "dan" and audit["action"] == "deploy.cancel"
        steps = client.app.state.store.get_deploy(did)["steps"]
        assert steps[-1]["output"] == "cancelled by dan"


def test_step_output_is_hidden_without_admin_token(env):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        did = store.create_deploy("app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "ci")
        store.add_step(did, "migrate", "failed", output="Login failed for user sa")
        public = client.get(f"/deploys/{did}").json()["steps"][0]
        assert public["status"] == "failed" and public["output"] is None
        private = client.get(f"/deploys/{did}", headers=ADMIN).json()["steps"][0]
        assert private["output"] == "Login failed for user sa"
        wrong = client.get(f"/deploys/{did}", headers={"X-Admin-Token": "x" * 32}).json()
        assert wrong["steps"][0]["output"] is None


def test_healthz_reports_db_and_worker(env):
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok", "db": "ok", "worker": "ok"}
        client.app.state.store.check_writable = lambda: (_ for _ in ()).throw(RuntimeError("ro"))
        assert client.get("/healthz").json()["status"] == "degraded"


def test_status_endpoint_summarizes_app(env):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        assert client.get("/admin/apps/app-x/status", headers=ADMIN).json() == {
            "app": "app-x",
            "frozen": False,
            "busy": False,
            "queued": 0,
            "current_release": None,
            "last_deploy": None,
            "last_health": None,
        }
        did = store.create_deploy("app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "ci")
        store.set_status(did, "succeeded", finished=True)
        store.add_step(did, "health", "succeeded", output="healthy after 1 attempt(s)")
        status = client.get("/admin/apps/app-x/status", headers=ADMIN).json()
        assert status["last_deploy"]["deploy_id"] == did
        assert status["last_health"]["output"] == "healthy after 1 attempt(s)"
        assert client.get("/admin/apps/nope/status", headers=ADMIN).status_code == 404


@pytest.mark.parametrize("kind", ["cleanup", "connect", "remove", "activate"])
@pytest.mark.parametrize("deployment_status", [None, "failed", "succeeded"])
def test_status_keeps_local_operations_separate_from_last_deployment(env, kind, deployment_status):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        deployment = None
        if deployment_status:
            deployment = store.create_deploy(
                "app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "ci"
            )
            store.set_status(deployment, deployment_status, finished=True)
        operation = store.create_deploy(
            "app-x", "0" * 40, "local-operation://test", "0" * 64, "test", kind=kind
        )
        for operation_status in ("queued", "running", "succeeded"):
            store.set_status(operation, operation_status, finished=operation_status == "succeeded")
            status = client.get("/admin/apps/app-x/status", headers=ADMIN).json()
            assert status["busy"] == (operation_status != "succeeded")
            if deployment is None:
                assert status["last_deploy"] is None
            else:
                assert status["last_deploy"]["deploy_id"] == deployment
                assert status["last_deploy"]["status"] == deployment_status
        history = client.get("/admin/deploys?app=app-x", headers=ADMIN).json()
        assert history[0]["deploy_id"] == operation and history[0]["kind"] == kind


def test_history_filters_and_pagination(env):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        ids = [
            store.create_deploy("app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "ci")
            for _ in range(3)
        ]
        store.set_status(ids[0], "failed", finished=True)
        rows = client.get("/admin/deploys?status=failed", headers=ADMIN).json()
        assert [row["deploy_id"] for row in rows] == [ids[0]]
        rows = client.get("/admin/deploys?limit=1&offset=1", headers=ADMIN).json()
        assert [row["deploy_id"] for row in rows] == [ids[1]]
        assert client.get("/admin/deploys?status=bogus", headers=ADMIN).status_code == 422
        assert client.get("/admin/deploys?app=Bad_Name", headers=ADMIN).status_code == 422


def test_redeploy_refuses_local_operations(env):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        did = store.create_deploy(
            "app-x", "0" * 40, "local-release://previous", "0" * 64, "activate:previous", "activate"
        )
        response = client.post(f"/admin/deploys/{did}/redeploy", headers=ADMIN)
        assert response.status_code == 409
        assert "artifact" in response.json()["detail"]


def test_mutations_are_audited_with_proxy_user(env):
    with TestClient(create_app()) as client:
        headers = {**ADMIN, "X-Remote-User": "dan@example.com"}
        client.post("/admin/apps/app-x/freeze", headers=headers, json={"frozen": True})
        client.post(
            "/admin/apps/app-x/freeze",
            headers={**ADMIN, "X-Remote-User": "bad user!"},
            json={"frozen": False},
        )
        rows = client.get("/admin/audit?limit=2", headers=ADMIN).json()
        assert [row["actor"] for row in rows] == ["admin-token", "dan@example.com"]
        assert rows[1]["action"] == "app.freeze" and rows[1]["target"] == "app-x"
        assert client.get("/admin/audit").status_code == 401


def test_non_ascii_admin_token_is_rejected_not_crashed(env):
    with TestClient(create_app()) as client:
        response = client.get(
            "/admin/apps", headers={b"X-Admin-Token": ("tok\xe9n" + "a" * 30).encode("latin-1")}
        )
        assert response.status_code == 401


def test_setup_accepts_new_spec_fields(env):
    with TestClient(create_app()) as client:
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        spec.pop("secret")
        spec.pop("github")
        spec["hooks"] = {"before_cutover": ["true"], "after_health": None, "timeout_seconds": 30}
        spec["health"]["expect_body"] = "{commit_sha}"
        spec["health"]["expect_header"] = "X-Release: {commit_sha}"
        spec["migrate"]["timeout_seconds"] = 1200
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 200
        saved = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        assert saved["hooks"]["before_cutover"] == ["true"]
        assert saved["health"]["expect_header"] == "X-Release: {commit_sha}"
        assert saved["migrate"]["timeout_seconds"] == 1200
        spec["health"]["expect_header"] = "no colon"
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 422
