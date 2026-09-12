import pytest
from fastapi.testclient import TestClient

from deployd import config
from deployd.api import admin
from deployd.main import create_app
from deployd.worker import runner

ADMIN = {"X-Admin-Token": "a" * 32}
TOKEN = "github_pat_" + "t" * 40


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "MANAGED_ROOT", tmp_path)
    (tmp_path / "apps.yaml").write_text("apps: {}\n")
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(tmp_path / "apps.yaml"))
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", ADMIN["X-Admin-Token"])
    for key in ("DEPLOYD_SECRET_SITE", "DEPLOYD_GITHUB_TOKEN_SITE", "DEPLOYD_GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


def payload(root, **credentials):
    root = root / "site"
    return {
        "create_only": True,
        "spec": {
            "github_repository": "acme/site",
            "deploy_url": "https://deploy.example.com/",
            "releases_dir": str(root / "releases"),
            "current_link": str(root / "releases/current"),
            "release_layout": "directory",
            "artifact": {
                "allowed_url_prefix": "https://api.github.com/repos/acme/site/releases/assets/",
                "allowed_redirect_hosts": ["release-assets.githubusercontent.com"],
            },
            "restart": {
                "command": ["/usr/bin/test", "-s", str(root / "releases/current/index.html")]
            },
            "health": {"url": "https://example.com"},
        },
        "credentials": credentials,
    }


def test_setup_persists_credentials_without_exposing_them(setup_env):
    with TestClient(create_app()) as client:
        body = payload(setup_env, generate_signing_secret=True, github_token=TOKEN)
        assert client.post("/admin/apps/site/setup", json=body).status_code == 401
        response = client.post("/admin/apps/site/setup", headers=ADMIN, json=body)
        assert response.status_code == 200
        secret = response.json()["secret"]
        assert len(secret) == 64
        assert TOKEN not in response.text
        config.get_app_registry.cache_clear()
        assert config.get_app_secret("site") == secret
        assert config.get_app_github_token("site") == TOKEN
        assert (setup_env / "secrets.env").stat().st_mode & 0o777 == 0o600
        listing = client.get("/admin/apps", headers=ADMIN)
        assert TOKEN not in listing.text and secret not in listing.text
        assert TOKEN not in (setup_env / "apps.yaml").read_text()
        assert secret not in (setup_env / "apps.yaml").read_text()
        assert listing.json()["site"]["github"] == {
            "configured": True,
            "source": "app",
            "env_override": False,
        }
        assert listing.json()["site"]["deploy_url"] == "https://deploy.example.com"
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 409
        assert config.get_app_secret("site") == secret
        body.update(create_only=False, credentials={})
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 200
        assert config.get_app_secret("site") == secret
        assert config.get_app_github_token("site") == TOKEN
        assert client.delete("/admin/apps/site", headers=ADMIN).status_code == 200
        assert config.get_app_secret("site") is None
        assert config.get_app_github_token("site") is None


@pytest.mark.parametrize(
    "credentials",
    [
        {"github_token": "short-sensitive-value"},
        {"signing_secret": "short-sensitive-value"},
        {"signing_secret": "s" * 32, "generate_signing_secret": True},
        {"github_token": TOKEN, "remove_github_token": True},
        {"github_token": TOKEN + "\nINJECT=bad"},
    ],
)
def test_setup_validation_never_reflects_credentials(setup_env, credentials):
    with TestClient(create_app()) as client:
        response = client.post(
            "/admin/apps/site/setup", headers=ADMIN, json=payload(setup_env, **credentials)
        )
        assert response.status_code == 422
        for value in credentials.values():
            if isinstance(value, str):
                assert value not in response.text
        assert all("input" not in error for error in response.json()["detail"])
        assert config.get_app_registry() == {}


def test_setup_failed_config_write_restores_credentials(setup_env, monkeypatch):
    config.set_app_secret("site", "s" * 32)
    original = (setup_env / "secrets.env").read_text()

    def fail(*args):
        raise OSError("cannot write")

    monkeypatch.setattr(config, "upsert_app", fail)
    with TestClient(create_app()) as client:
        response = client.post(
            "/admin/apps/site/setup",
            headers=ADMIN,
            json=payload(setup_env, generate_signing_secret=True, github_token=TOKEN),
        )
        assert response.status_code == 409
    assert (setup_env / "secrets.env").read_text() == original
    assert config.get_app_registry() == {}


def test_setup_environment_credentials_are_not_overwritten(setup_env, monkeypatch):
    with TestClient(create_app()) as client:
        for key, credentials in [
            ("DEPLOYD_SECRET_SITE", {"generate_signing_secret": True}),
            ("DEPLOYD_GITHUB_TOKEN_SITE", {"github_token": TOKEN}),
            ("DEPLOYD_GITHUB_TOKEN_SITE", {"remove_github_token": True}),
        ]:
            monkeypatch.setenv(key, "e" * 32)
            response = client.post(
                "/admin/apps/site/setup", headers=ADMIN, json=payload(setup_env, **credentials)
            )
            assert response.status_code == 409
            assert config.get_app_registry() == {}
            monkeypatch.delenv(key)


def test_busy_app_rejects_setup_before_touching_credentials(setup_env, monkeypatch):
    with TestClient(create_app()) as client:
        body = payload(setup_env, signing_secret="s" * 32)
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 200
        original = (setup_env / "secrets.env").read_text()
        monkeypatch.setattr(client.app.state.store, "has_active_deploys", lambda name: True)
        body.update(
            create_only=False, credentials={"generate_signing_secret": True, "github_token": TOKEN}
        )
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 409
        assert (setup_env / "secrets.env").read_text() == original


def test_setup_unknown_edit_does_not_create_credentials(setup_env):
    body = payload(setup_env, generate_signing_secret=True)
    body["create_only"] = False
    with TestClient(create_app()) as client:
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 404
    assert not (setup_env / "secrets.env").exists()


def test_setup_paths_are_managed_and_site_files_are_untouched(setup_env):
    site = setup_env / "www/example.com"
    site.mkdir(parents=True)
    (site / "index.html").write_text("existing live site")
    body = payload(setup_env, generate_signing_secret=True)
    body["spec"]["site_path"] = str(site)
    with TestClient(create_app()) as client:
        response = client.post("/admin/apps/site/setup", headers=ADMIN, json=body)
        assert response.status_code == 200
        assert response.json()["config"]["site_path"] == str(site)
        body["create_only"] = False
        body["credentials"] = {}
        body["spec"]["site_path"] = str(setup_env / "www/other.com")
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 409
    assert (site / "index.html").read_text() == "existing live site"
    assert not site.is_symlink()


def test_custom_internal_paths_are_rejected_before_any_directory_creation(setup_env):
    body = payload(setup_env, generate_signing_secret=True)
    unexpected = setup_env / "unexpected"
    body["spec"].update(releases_dir=str(unexpected), current_link=str(unexpected / "current"))
    with TestClient(create_app()) as client:
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 422
    assert not unexpected.exists()
    assert not (setup_env / "secrets.env").exists()


def test_setup_app_token_is_scoped_and_removal_uses_fallback(setup_env, monkeypatch):
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "fallback-token")
    config.get_settings.cache_clear()
    with TestClient(create_app()) as client:
        body = payload(setup_env, signing_secret="s" * 32, github_token=TOKEN)
        response = client.post("/admin/apps/site/setup", headers=ADMIN, json=body)
        assert response.status_code == 200 and response.json()["secret"] is None
        assert client.get("/admin/setup", headers=ADMIN).json()["github_server_token_configured"]
        url = "https://api.github.com/repos/acme/site/releases/assets/123"
        assert runner._github_asset_headers(url, "site")["Authorization"] == f"Bearer {TOKEN}"
        assert (
            runner._github_asset_headers(url, "other")["Authorization"] == "Bearer fallback-token"
        )
        assert (
            runner._github_asset_headers(
                "https://release-assets.githubusercontent.com/a.zip", "site"
            )
            == {}
        )
        body.update(create_only=False, credentials={"remove_github_token": True})
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 200
        assert config.get_app_github_token("site") == "fallback-token"
        assert config.get_app_secret("site") == "s" * 32


@pytest.mark.parametrize(
    "field,value",
    [
        ("github_repository", "../site"),
        ("github_repository", "other/repo"),
        ("deploy_url", "https://secret@example.com"),
        ("deploy_url", "https://example.com?token=secret"),
    ],
)
def test_setup_rejects_invalid_repository_and_url(setup_env, field, value):
    body = payload(setup_env)
    body["spec"][field] = value
    with TestClient(create_app()) as client:
        assert client.post("/admin/apps/site/setup", headers=ADMIN, json=body).status_code == 422
