import pytest
from fastapi.testclient import TestClient

from deployd import config, envfile
from deployd.config import AppSpec
from deployd.main import create_app

ADMIN_TOKEN = "a" * 32
ADMIN = {"X-Admin-Token": ADMIN_TOKEN, "X-Remote-User": "dan"}
SHA = "c" * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    apps_yaml = tmp_path / "apps.yaml"
    apps_yaml.write_text(
        f"""
apps:
  app-x:
    env_file: /etc/app-x/app-x.env
    releases_dir: {tmp_path}/releases
    current_link: {tmp_path}/current
    artifact:
      allowed_url_prefix: "https://example.com/"
    restart:
      command: ["true"]
  plain:
    releases_dir: {tmp_path}/plain/releases
    current_link: {tmp_path}/plain/current
    artifact:
      allowed_url_prefix: "https://example.com/"
    restart:
      command: ["true"]
"""
    )
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(apps_yaml))
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", ADMIN_TOKEN)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    helper = tmp_path / "env-file"
    helper.write_text("")
    monkeypatch.setattr(envfile, "HELPER", helper)
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


@pytest.fixture
def helper(monkeypatch):
    calls = []
    answers = {
        "check": {"exists": True, "mode": "0640", "backups": 1},
        "read": {"content": "DB_PASSWORD=hunter2\n", "sha256": SHA, "mode": "0640"},
        "write": {
            "saved": True,
            "sha256": "d" * 64,
            "added": ["NEW"],
            "removed": [],
            "changed": ["DB_PASSWORD"],
            "backups": 2,
        },
        "restore": {
            "saved": True,
            "sha256": SHA,
            "added": [],
            "removed": ["NEW"],
            "changed": [],
            "backups": 3,
        },
    }

    def invoke(operation, name, spec, payload=None):
        calls.append((operation, name, str(spec.env_file), payload))
        return answers[operation]

    monkeypatch.setattr(envfile, "_invoke", invoke)
    return calls, answers


def test_env_endpoints_require_the_admin_token_and_a_configured_file(env, helper):
    with TestClient(create_app()) as client:
        base = "/admin/apps/app-x/env"
        assert client.get(base).status_code == 401
        assert client.get(f"{base}/content").status_code == 401
        assert client.put(base, json={"content": "A=1\n"}).status_code == 401
        assert client.get("/admin/apps/plain/env", headers=ADMIN).status_code == 409
        assert client.get("/admin/apps/missing/env", headers=ADMIN).status_code == 404
        assert helper[0] == []


def test_status_reports_metadata_without_content(env, helper):
    with TestClient(create_app()) as client:
        body = client.get("/admin/apps/app-x/env", headers=ADMIN).json()
        assert body == {
            "path": "/etc/app-x/app-x.env",
            "available": True,
            "exists": True,
            "mode": "0640",
            "backups": 1,
        }
        assert "env." not in client.get("/admin/audit", headers=ADMIN).text


def test_status_says_when_the_helper_is_not_installed(env, helper, monkeypatch):
    monkeypatch.setattr(envfile, "HELPER", env / "absent")
    with TestClient(create_app()) as client:
        body = client.get("/admin/apps/app-x/env", headers=ADMIN).json()
        assert body == {"path": "/etc/app-x/app-x.env", "available": False}
        assert helper[0] == []


def test_reading_content_is_audited_without_values(env, helper):
    with TestClient(create_app()) as client:
        body = client.get("/admin/apps/app-x/env/content", headers=ADMIN).json()
        assert body["content"] == "DB_PASSWORD=hunter2\n" and body["sha256"] == SHA
        audit = client.get("/admin/audit", headers=ADMIN).json()
        assert audit[0]["action"] == "env.read" and audit[0]["actor"] == "dan"
        assert "hunter2" not in str(audit)


def test_write_passes_content_to_the_helper_and_audits_key_names_only(env, helper):
    calls, _ = helper
    with TestClient(create_app()) as client:
        queued = []
        client.app.state.queue.enqueue_restart = lambda *args: queued.append(args)
        response = client.put(
            "/admin/apps/app-x/env",
            headers=ADMIN,
            json={"content": "DB_PASSWORD=s3cret\nNEW=1\n", "expected_sha256": SHA},
        )
        assert response.status_code == 200
        assert response.json()["deploy_id"] is None and queued == []
        assert calls == [
            (
                "write",
                "app-x",
                "/etc/app-x/app-x.env",
                {"content": "DB_PASSWORD=s3cret\nNEW=1\n", "expected_sha256": SHA},
            )
        ]
        audit = client.get("/admin/audit", headers=ADMIN).json()[0]
        assert audit["action"] == "env.write"
        assert audit["detail"] == "changed: DB_PASSWORD; added: NEW"
        assert "s3cret" not in str(audit)


def test_write_can_queue_a_restart_of_the_live_release(env, helper):
    with TestClient(create_app()) as client:
        queued = []
        client.app.state.queue.enqueue_restart = lambda *args: queued.append(args)
        body = client.put(
            "/admin/apps/app-x/env", headers=ADMIN, json={"content": "A=1\n", "restart": True}
        ).json()
        assert queued == [("app-x", body["deploy_id"])]
        row = client.app.state.store.get_deploy(body["deploy_id"])
        assert row["kind"] == "restart" and row["triggered_by"] == "restart:app-x"
        assert (
            client.post(f"/admin/deploys/{body['deploy_id']}/redeploy", headers=ADMIN).status_code
            == 409
        )


def test_unchanged_content_neither_audits_nor_restarts(env, helper):
    helper[1]["write"] = {"saved": False, "sha256": SHA, "added": [], "removed": [], "changed": []}
    with TestClient(create_app()) as client:
        queued = []
        client.app.state.queue.enqueue_restart = lambda *args: queued.append(args)
        body = client.put(
            "/admin/apps/app-x/env", headers=ADMIN, json={"content": "A=1\n", "restart": True}
        ).json()
        assert body["deploy_id"] is None and queued == []
        assert "env.write" not in client.get("/admin/audit", headers=ADMIN).text


def test_helper_errors_surface_as_conflicts_and_validation_never_echoes_content(
    env, helper, monkeypatch
):
    def refuse(*args):
        raise envfile.EnvFileError(409, "the file changed on the server after it was loaded")

    with TestClient(create_app()) as client:
        bad = client.put(
            "/admin/apps/app-x/env", headers=ADMIN, json={"content": "TOKEN=abc", "restart": "x"}
        )
        assert bad.status_code == 422 and "abc" not in bad.text
        monkeypatch.setattr(envfile, "_invoke", refuse)
        response = client.put("/admin/apps/app-x/env", headers=ADMIN, json={"content": "A=1\n"})
        assert response.status_code == 409 and "changed on the server" in response.text


def test_restore_requires_typing_the_app_name(env, helper):
    with TestClient(create_app()) as client:
        url = "/admin/apps/app-x/env/restore"
        assert client.post(url, headers=ADMIN, json={"confirm": "nope"}).status_code == 422
        assert helper[0] == []
        body = client.post(url, headers=ADMIN, json={"confirm": "app-x"}).json()
        assert body["saved"] and body["deploy_id"] is None
        audit = client.get("/admin/audit", headers=ADMIN).json()[0]
        assert (audit["action"], audit["detail"]) == ("env.restore", "removed: NEW")


BASE = {
    "releases_dir": "/srv/deployd/x/releases",
    "current_link": "/srv/deployd/x/current",
    "artifact": {"allowed_url_prefix": "https://example.com/"},
    "restart": {"command": ["true"]},
}


@pytest.mark.parametrize(
    "path", ["/etc/app/app.env", "/opt/astral/back/.env", "/etc/app/.env.production"]
)
def test_env_file_paths_are_accepted(path):
    assert str(AppSpec.model_validate({**BASE, "env_file": path}).env_file) == path


@pytest.mark.parametrize(
    "path",
    [
        "relative/app.env",
        "/etc/shadow",
        "/etc/app/config.yaml",
        "/opt/deployd/.env",
        "/var/lib/deployd/secrets.env",
        "/etc/app/../deployd/x.env",
    ],
)
def test_env_file_paths_are_rejected(path):
    with pytest.raises(ValueError):
        AppSpec.model_validate({**BASE, "env_file": path})


def test_helper_client_reports_a_missing_or_silent_helper(tmp_path, monkeypatch):
    spec = AppSpec.model_validate({**BASE, "env_file": "/etc/app/app.env"})
    monkeypatch.setattr(envfile, "HELPER", tmp_path / "absent")
    with pytest.raises(envfile.EnvFileError) as missing:
        envfile._invoke("check", "x", spec)
    assert missing.value.status == 503
    with pytest.raises(envfile.EnvFileError) as unset:
        envfile._invoke("check", "x", AppSpec.model_validate(BASE))
    assert unset.value.status == 409
