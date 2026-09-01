import pytest
from fastapi.testclient import TestClient

from deployd import config
from deployd.config import get_app_secret
from deployd.main import create_app

ADMIN_TOKEN = "a" * 32
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}


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
"""
    )
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(apps_yaml))
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.delenv("DEPLOYD_SECRET_APP_X", raising=False)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


def test_admin_requires_token(env):
    with TestClient(create_app()) as client:
        assert client.get("/admin/apps").status_code == 401
        assert client.get("/admin/apps", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_list_apps_shows_secret_state_not_secret(env):
    with TestClient(create_app()) as client:
        apps = client.get("/admin/apps", headers=ADMIN).json()
        assert apps["app-x"]["secret"] == {
            "configured": False,
            "fingerprint": None,
            "env_override": False,
        }
        assert "secret" not in str(apps["app-x"]["restart"])


def test_rotate_secret_returns_once_and_persists(env):
    with TestClient(create_app()) as client:
        out = client.post("/admin/apps/app-x/rotate-secret", headers=ADMIN).json()
        assert len(out["secret"]) == 64
        assert get_app_secret("app-x") == out["secret"]

        apps = client.get("/admin/apps", headers=ADMIN).json()
        assert apps["app-x"]["secret"]["configured"] is True
        assert apps["app-x"]["secret"]["fingerprint"] == out["fingerprint"]
        assert (env / "secrets.env").exists()


def test_rotate_unknown_app_404(env):
    with TestClient(create_app()) as client:
        assert client.post("/admin/apps/nope/rotate-secret", headers=ADMIN).status_code == 404


def test_rotate_env_managed_secret_is_rejected(env, monkeypatch):
    monkeypatch.setenv("DEPLOYD_SECRET_APP_X", "managed-externally")
    with TestClient(create_app()) as client:
        response = client.post("/admin/apps/app-x/rotate-secret", headers=ADMIN)
        assert response.status_code == 409
        assert not (env / "secrets.env").exists()


def test_upsert_app_persists_to_yaml(env):
    with TestClient(create_app()) as client:
        spec = {
            "releases_dir": str(env / "r2"),
            "current_link": str(env / "c2"),
            "artifact": {"allowed_url_prefix": "https://example.org/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/hz"},
        }
        assert client.put("/admin/apps/app-y", headers=ADMIN, json=spec).status_code == 200
        apps = client.get("/admin/apps", headers=ADMIN).json()
        assert set(apps) == {"app-x", "app-y"}
        assert "app-y" in (env / "apps.yaml").read_text()


def test_upsert_rejects_invalid_backend_app_name(env):
    with TestClient(create_app()) as client:
        spec = {
            "releases_dir": str(env / "r2"),
            "current_link": str(env / "c2"),
            "artifact": {"allowed_url_prefix": "https://example.org/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/hz"},
        }
        response = client.put("/admin/apps/BAD_NAME", headers=ADMIN, json=spec)
        assert response.status_code == 422


def test_upsert_rejects_active_deployments(env):
    with TestClient(create_app()) as client:
        client.app.state.store.create_deploy(
            "app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "test"
        )
        spec = {
            "releases_dir": str(env / "changed-releases"),
            "current_link": str(env / "changed-current"),
            "artifact": {"allowed_url_prefix": "https://example.org/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/hz"},
        }
        response = client.put("/admin/apps/app-x", headers=ADMIN, json=spec)
        assert response.status_code == 409
        assert client.get("/admin/apps", headers=ADMIN).json()["app-x"]["releases_dir"] == str(
            env / "releases"
        )


def test_delete_app(env):
    with TestClient(create_app()) as client:
        assert client.delete("/admin/apps/app-x", headers=ADMIN).status_code == 200
        assert client.get("/admin/apps", headers=ADMIN).json() == {}
        assert client.delete("/admin/apps/app-x", headers=ADMIN).status_code == 404


def test_delete_app_scrubs_its_secret(env):
    with TestClient(create_app()) as client:
        client.post("/admin/apps/app-x/rotate-secret", headers=ADMIN)
        assert "DEPLOYD_SECRET_APP_X=" in (env / "secrets.env").read_text()

        client.delete("/admin/apps/app-x", headers=ADMIN)
        assert "DEPLOYD_SECRET_APP_X=" not in (env / "secrets.env").read_text()
        assert get_app_secret("app-x") is None


def test_delete_app_rejects_active_deployments(env):
    with TestClient(create_app()) as client:
        client.app.state.store.create_deploy(
            "app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "test"
        )
        response = client.delete("/admin/apps/app-x", headers=ADMIN)
        assert response.status_code == 409
        assert "app-x" in client.get("/admin/apps", headers=ADMIN).json()


def test_delete_app_rejects_env_managed_secret(env, monkeypatch):
    monkeypatch.setenv("DEPLOYD_SECRET_APP_X", "managed-externally")
    with TestClient(create_app()) as client:
        response = client.delete("/admin/apps/app-x", headers=ADMIN)
        assert response.status_code == 409
        assert "app-x" in client.get("/admin/apps", headers=ADMIN).json()


def test_redeploy_requeues_same_artifact(env, monkeypatch):
    from deployd.worker import runner

    async def boom(spec, deploy, ctx):
        raise RuntimeError("stop early")

    monkeypatch.setitem(runner._STEP_FNS, "download", boom)
    with TestClient(create_app()) as client:
        store = client.app.state.store
        did = store.create_deploy("app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "test")
        out = client.post(f"/admin/deploys/{did}/redeploy", headers=ADMIN).json()
        assert out["deploy_id"] != did

        new = store.get_deploy(out["deploy_id"])
        assert new["commit_sha"] == "a" * 40
        assert new["artifact_url"] == "https://example.com/a.zip"
        assert new["triggered_by"] == f"redeploy:{did[:8]}"

        assert client.post("/admin/deploys/nope/redeploy", headers=ADMIN).status_code == 404


def test_redeploy_rejects_artifact_disallowed_by_current_config(env):
    with TestClient(create_app()) as client:
        store = client.app.state.store
        did = store.create_deploy("app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "test")
        store.set_status(did, "succeeded", finished=True)
        spec = {
            "releases_dir": str(env / "releases"),
            "current_link": str(env / "current"),
            "artifact": {"allowed_url_prefix": "https://example.org/"},
            "restart": {"command": ["true"]},
            "health": {"url": "http://127.0.0.1:1/hz"},
        }
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 200

        response = client.post(f"/admin/deploys/{did}/redeploy", headers=ADMIN)
        assert response.status_code == 409
        assert response.json()["detail"] == "artifact URL is no longer allowed"


def test_list_deploys(env):
    with TestClient(create_app()) as client:
        app_obj = client.app
        did = app_obj.state.store.create_deploy(
            "app-x", "a" * 40, "https://example.com/a.zip", "b" * 64, "test"
        )
        rows = client.get("/admin/deploys", headers=ADMIN).json()
        assert [r["deploy_id"] for r in rows] == [did]
        assert client.get("/admin/deploys?app=other", headers=ADMIN).json() == []
