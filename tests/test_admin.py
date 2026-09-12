import base64
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from deployd import config
from deployd.config import get_app_secret
from deployd.main import create_app
from deployd.worker import directory_layout, runner

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


def test_github_setup_requires_admin_and_configured_repository(env):
    with TestClient(create_app()) as client:
        endpoint = "/admin/apps/app-x/github-actions"
        assert client.post(endpoint, json={}).status_code == 401
        assert client.post(endpoint, json={}, headers=ADMIN).status_code == 422
        assert (
            client.post("/admin/apps/missing/github-actions", json={}, headers=ADMIN).status_code
            == 404
        )


def test_github_setup_saves_only_build_metadata_and_downloads_no_secrets(env):
    with TestClient(create_app()) as client:
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        spec.update(github_repository="acme/site", deploy_url="https://deployd.example.com")
        spec["artifact"]["allowed_url_prefix"] = (
            "https://api.github.com/repos/acme/site/releases/assets/"
        )
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 200
        config.set_app_secret("app-x", "signing-secret-" + "s" * 32)
        endpoint = "/admin/apps/app-x/github-actions"
        response = client.post(endpoint, json={"project_dir": "frontend"}, headers=ADMIN)
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "app-x-github-actions.zip"
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(data["content_base64"])))
        for path in archive.namelist():
            assert ADMIN_TOKEN.encode() not in archive.read(path)
            assert b"signing-secret-" not in archive.read(path)
        saved = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        assert saved["github_actions"]["project_dir"] == "frontend"
        assert saved["github_actions"]["automatic"] is False
        for key in ("restart", "health", "artifact", "current_link", "releases_dir"):
            assert saved[key] == spec[key]
        assert config.get_app_secret("app-x") == "signing-secret-" + "s" * 32
        assert client.get("/admin/deploys", headers=ADMIN).json() == []
        assert (
            client.post(endpoint, json={"output_dir": "../../etc"}, headers=ADMIN).status_code
            == 422
        )
        assert (
            client.get("/admin/apps", headers=ADMIN).json()["app-x"]["github_actions"]
            == saved["github_actions"]
        )


def test_real_directory_app_can_be_configured_and_activated_through_api(env, monkeypatch):
    async def healthy(*args):
        return "healthy"

    monkeypatch.setitem(runner._STEP_FNS, "health", healthy)
    with TestClient(create_app()) as client:
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        spec.update(release_layout="directory", current_link=str(env / "releases/current"))
        response = client.put("/admin/apps/app-x", headers=ADMIN, json=spec)
        assert response.status_code == 200
        assert (env / "releases").is_dir()
        old = saved_release(client, env)
        new = saved_release(client, env, "b")
        for path in (old, new):
            (path / "index.html").write_text(path.name[0])
            directory_layout.initialize(path)
        new.rename(env / "releases/current")
        metadata = directory_layout.read_manifest(env / "releases/current")
        metadata["previous"] = old.name
        directory_layout.write_manifest(env / "releases/current", metadata)
        base = "/admin/apps/app-x/releases"
        rows = client.get(base, headers=ADMIN).json()["releases"]
        assert rows[0]["name"] == "current" and rows[0]["release_id"] == new.name
        assert not rows[0]["can_activate"] and rows[1]["can_activate"]
        assert (
            client.post(base + "/cleanup", headers=ADMIN, json={"release": old.name}).status_code
            == 409
        )
        response = client.post(base + "/activate", headers=ADMIN, json={"release": old.name})
        assert response.status_code == 202
        did = response.json()["deploy_id"]
        for _ in range(200):
            row = client.app.state.store.get_deploy(did)
            if row["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert row["status"] == "succeeded"
        assert (env / "releases/current/index.html").read_text() == "a"
        assert not (env / "releases/current").is_symlink()
        spec.update(release_layout="symlink", current_link=str(env / "current"))
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 409


def test_layout_change_cannot_move_existing_live_symlink(env):
    with TestClient(create_app()) as client:
        old = saved_release(client, env)
        (env / "current").symlink_to(old, target_is_directory=True)
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        spec.update(release_layout="directory", current_link=str(env / "releases/current"))
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 409
        assert (env / "current").resolve() == old


def test_bad_release_metadata_does_not_disable_management(env):
    with TestClient(create_app()) as client:
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        spec.update(release_layout="directory", current_link=str(env / "releases/current"))
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 200
    (env / "releases/current").mkdir()
    (env / "releases/current/index.html").write_text("unmanaged site")
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/admin/apps", headers=ADMIN).status_code == 200
        response = client.get("/admin/apps/app-x/releases", headers=ADMIN)
        assert response.status_code == 409 and "unmanaged" in response.json()["detail"]
    assert (env / "releases/current/index.html").read_text() == "unmanaged site"


def saved_release(client, root, sha="a", status="succeeded"):
    store = client.app.state.store
    deploy_id = store.create_deploy(
        "app-x", sha * 40, "https://example.com/a.zip", "b" * 64, "test"
    )
    store.set_status(deploy_id, status, finished=True)
    path = root / "releases" / f"{sha * 40}-{deploy_id}"
    path.mkdir(parents=True)
    return path


def test_release_endpoints_require_admin(env):
    with TestClient(create_app()) as client:
        base = "/admin/apps/app-x/releases"
        assert client.get(base).status_code == 401
        for action in ("activate", "cleanup"):
            assert client.post(f"{base}/{action}", json={"release": "previous"}).status_code == 401


def test_local_activation_is_queued_and_locks_cleanup(env, monkeypatch):
    with TestClient(create_app()) as client:
        previous = saved_release(client, env)
        current = saved_release(client, env, "c")
        (env / "current").symlink_to(current, target_is_directory=True)
        (env / "current.previous").symlink_to(previous, target_is_directory=True)
        base = "/admin/apps/app-x/releases"
        releases = client.get(base, headers=ADMIN).json()["releases"]
        assert next(row for row in releases if row["name"] == previous.name)["can_activate"]
        assert not next(row for row in releases if row["name"] == current.name)["can_activate"]
        queued = []
        monkeypatch.setattr(
            client.app.state.queue, "enqueue_activation", lambda *args: queued.append(args)
        )
        response = client.post(f"{base}/activate", headers=ADMIN, json={"release": previous.name})
        assert response.status_code == 202
        did = response.json()["deploy_id"]
        assert queued == [("app-x", did, previous.name)]
        assert client.app.state.store.get_deploy(did)["triggered_by"] == f"activate:{previous.name}"
        for action in ("activate", "cleanup"):
            assert (
                client.post(
                    f"{base}/{action}", headers=ADMIN, json={"release": previous.name}
                ).status_code
                == 409
            )


def test_cleanup_deletes_only_selected_files_and_preserves_history(env):
    with TestClient(create_app()) as client:
        old = saved_release(client, env)
        current = saved_release(client, env, "c")
        (env / "current").symlink_to(current, target_is_directory=True)
        base = "/admin/apps/app-x/releases"
        assert (
            client.post(
                f"{base}/cleanup", headers=ADMIN, json={"release": current.name}
            ).status_code
            == 409
        )
        response = client.post(f"{base}/cleanup", headers=ADMIN, json={"release": old.name})
        assert response.status_code == 200
        assert current.exists() and not old.exists()
        assert client.app.state.store.get_deploy(old.name[41:])["status"] == "succeeded"


def test_activation_rejects_failed_unknown_and_invalid_releases(env):
    with TestClient(create_app()) as client:
        failed = saved_release(client, env, status="failed")
        base = "/admin/apps/app-x/releases"
        assert not client.get(base, headers=ADMIN).json()["releases"][0]["can_activate"]
        assert (
            client.post(
                f"{base}/activate", headers=ADMIN, json={"release": failed.name}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"{base}/activate", headers=ADMIN, json={"release": "../outside"}
            ).status_code
            == 422
        )
        assert client.get("/admin/apps/unknown/releases", headers=ADMIN).status_code == 404


def test_retention_choice_persists_without_deleting_existing_versions(env):
    with TestClient(create_app()) as client:
        old = saved_release(client, env)
        spec = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        assert spec["keep_previous"] == 1
        spec["keep_previous"] = 0
        spec["auto_cleanup"] = False
        assert client.put("/admin/apps/app-x", headers=ADMIN, json=spec).status_code == 200
        config.get_app_registry.cache_clear()
        saved = client.get("/admin/apps", headers=ADMIN).json()["app-x"]
        assert saved["keep_previous"] == 0 and saved["auto_cleanup"] is False
        assert old.exists()


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
