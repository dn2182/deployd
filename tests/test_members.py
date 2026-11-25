"""Team members: accounts limited to the applications assigned to them."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from deployd import auth, config, envfile
from deployd.api import admin
from deployd.main import create_app
from deployd.store.db import Store

ADMIN = {"X-Admin-Token": "a" * 32}
PASSWORD = "correct-horse-battery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    apps = "apps:\n"
    for name in ("mine", "other"):
        apps += f"""  {name}:
    env_file: /etc/apps-env/{name}/{name}.env
    releases_dir: {tmp_path}/{name}/releases
    current_link: {tmp_path}/{name}/current
    artifact:
      allowed_url_prefix: "https://example.com/"
    restart:
      command: ["true"]
"""
    (tmp_path / "apps.yaml").write_text(apps)
    monkeypatch.setenv("DEPLOYD_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DEPLOYD_APPS_CONFIG", str(tmp_path / "apps.yaml"))
    monkeypatch.setenv("DEPLOYD_SECRETS_FILE", str(tmp_path / "secrets.env"))
    monkeypatch.setenv("DEPLOYD_ADMIN_TOKEN", "a" * 32)
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()
    monkeypatch.setattr(auth, "throttle", auth.LoginThrottle())
    monkeypatch.setattr(auth, "_SCRYPT", {"n": 2**4, "r": 8, "p": 1})
    helper = tmp_path / "env-file"
    helper.write_text("")
    monkeypatch.setattr(envfile, "HELPER", helper)
    monkeypatch.setattr(
        envfile,
        "_invoke",
        lambda operation, name, spec, payload=None: {
            "exists": True,
            "content": f"SECRET_OF={name}\n",
            "sha256": "c" * 64,
            "saved": True,
            "added": [],
            "removed": [],
            "changed": ["SECRET_OF"],
        },
    )
    yield tmp_path
    config.get_settings.cache_clear()
    config.get_app_registry.cache_clear()


@pytest.fixture
def client(env):
    with TestClient(create_app()) as value:
        yield value


def member(client, username="dev", apps=("mine",), password=PASSWORD):
    created = client.post(
        "/admin/users",
        headers=ADMIN,
        json={"username": username, "password": password, "apps": list(apps)},
    )
    assert created.status_code == 201, created.text
    return sign_in(client, username, password)


def sign_in(client, username="dev", password=PASSWORD):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-Deployd-Session": response.json()["token"]}


def deploy(client, app):
    return client.app.state.store.create_deploy(
        app, "a" * 40, "https://example.com/a", "b" * 64, "t"
    )


def test_member_sees_and_operates_only_assigned_apps(client):
    session = member(client)
    assert client.get("/admin/me", headers=session).json() == {
        "role": "member",
        "username": "dev",
        "apps": ["mine"],
    }
    assert set(client.get("/admin/apps", headers=session).json()) == {"mine"}
    assert client.get("/admin/apps/mine/status", headers=session).status_code == 200
    assert client.post(
        "/admin/apps/mine/freeze", headers=session, json={"frozen": True}
    ).json() == {
        "app": "mine",
        "frozen": True,
    }
    assert client.get("/admin/apps/mine/env/content", headers=session).json()["content"] == (
        "SECRET_OF=mine\n"
    )
    saved = client.put("/admin/apps/mine/env", headers=session, json={"content": "A=1\n"})
    assert saved.status_code == 200
    audit = client.get("/admin/audit", headers=ADMIN).json()
    assert [row["actor"] for row in audit if row["action"] == "env.write"] == ["dev"]


def test_member_cannot_reach_another_app_and_cannot_tell_it_exists(client):
    session = member(client)
    for method, path, body in [
        ("get", "/admin/apps/other/status", None),
        ("get", "/admin/apps/other/releases", None),
        ("get", "/admin/apps/other/env", None),
        ("get", "/admin/apps/other/env/content", None),
        ("put", "/admin/apps/other/env", {"content": "A=1\n"}),
        ("post", "/admin/apps/other/env/restore", {"confirm": "other"}),
        ("post", "/admin/apps/other/freeze", {"frozen": True}),
        ("post", "/admin/apps/other/releases/activate", {"release": "previous"}),
        ("post", "/admin/apps/other/releases/cleanup", {"release": "previous"}),
        ("get", "/admin/apps/other/website", None),
        ("post", "/admin/apps/other/github-actions/preview", {}),
        ("post", "/admin/apps/other/github-actions", {}),
    ]:
        response = getattr(client, method)(
            path, headers=session, **({"json": body} if body else {})
        )
        missing = getattr(client, method)(
            path.replace("other", "nothing"), headers=session, **({"json": body} if body else {})
        )
        assert response.status_code == 404, (path, response.status_code)
        assert response.json() == missing.json() == {"detail": "unknown app"}


def test_configuration_stays_with_the_administrator(client):
    session = member(client)
    spec = client.get("/admin/apps", headers=ADMIN).json()["mine"]
    spec = {key: value for key, value in spec.items() if key not in ("secret", "github")}
    for method, path, body in [
        ("put", "/admin/apps/mine", spec),
        ("post", "/admin/apps/mine/setup", {"spec": spec}),
        ("delete", "/admin/apps/mine", None),
        ("post", "/admin/apps/mine/rotate-secret", None),
        ("post", "/admin/apps/mine/website/connect", {"confirm": "mine"}),
        ("post", "/admin/apps/mine/website/repair", {"confirm": "mine"}),
        ("get", "/admin/setup", None),
        ("get", "/admin/account", None),
        ("post", "/admin/account/password", {"current_password": "x", "new_password": "y" * 12}),
        ("get", "/admin/users", None),
        ("post", "/admin/users", {"username": "eve", "password": PASSWORD, "apps": ["other"]}),
        ("patch", "/admin/users/dev", {"apps": ["mine", "other"]}),
        ("delete", "/admin/users/dev", None),
    ]:
        response = client.request(method.upper(), path, headers=session, json=body)
        assert response.status_code == 403, (path, response.status_code, response.text)
    assert set(client.get("/admin/apps", headers=session).json()) == {"mine"}


def test_every_management_route_rejects_a_member_without_access(client):
    """A new endpoint must be owner-only or app-scoped; nothing is open by accident."""
    session = member(client, apps=())
    open_to_members = {
        ("GET", "/admin/apps"),
        ("GET", "/admin/deploys"),
        ("GET", "/admin/audit"),
        ("GET", "/admin/me"),
        ("POST", "/admin/me/password"),
        ("POST", "/admin/logout"),
        ("POST", "/admin/deploys/{deploy_id}/cancel"),
        ("POST", "/admin/deploys/{deploy_id}/redeploy"),
    }
    checked = 0
    routes = [*admin.router.routes, *admin.app_router.routes, *admin.shared_router.routes]
    for route in routes:
        assert route.path.startswith("/admin")
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in open_to_members:
                continue
            path = route.path.replace("{name}", "mine").replace("{username}", "dev")
            response = client.request(method, path, headers=session, json={})
            assert response.status_code in (403, 404), (method, route.path, response.status_code)
            assert client.request(method, path, json={}).status_code == 401
            checked += 1
    assert checked >= 20
    assert client.get("/admin/apps", headers=session).json() == {}
    assert client.get("/admin/deploys", headers=session).json() == []


def test_deploy_history_output_and_audit_are_limited_to_assigned_apps(client):
    session = member(client)
    store = client.app.state.store
    mine, other = deploy(client, "mine"), deploy(client, "other")
    for did in (mine, other):
        store.add_step(did, "restart", "succeeded", output="DSN=secret")
    store.audit("admin-token", "app.freeze", "other", None, "other")
    store.audit("admin-token", "app.freeze", "mine", None, "mine")

    listed = client.get("/admin/deploys", headers=session).json()
    assert [row["deploy_id"] for row in listed] == [mine]
    assert client.get("/admin/deploys?app=other", headers=session).json() == []
    assert client.get(f"/deploys/{mine}", headers=session).json()["steps"][0]["output"] == (
        "DSN=secret"
    )
    assert client.get(f"/deploys/{other}", headers=session).json()["steps"][0]["output"] is None
    assert client.get(f"/deploys/{other}", headers=ADMIN).json()["steps"][0]["output"]
    assert client.post(f"/admin/deploys/{other}/cancel", headers=session).status_code == 404
    assert client.post(f"/admin/deploys/{other}/redeploy", headers=session).status_code == 404
    assert client.post(f"/admin/deploys/{mine}/cancel", headers=session).status_code == 200
    targets = {row["app"] for row in client.get("/admin/audit", headers=session).json()}
    assert targets == {"mine"}


def test_login_is_uniform_and_locks_after_repeated_failures(client):
    member(client)
    wrong = client.post("/auth/login", json={"username": "dev", "password": "nope-nope-nope"})
    unknown = client.post("/auth/login", json={"username": "ghost", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    for _ in range(auth.MAX_FAILURES):
        client.post("/auth/login", json={"username": "ghost", "password": "x" * 12})
        client.post("/auth/login", json={"username": "dev", "password": "x" * 12})
    locked = client.post("/auth/login", json={"username": "dev", "password": PASSWORD})
    assert locked.status_code == 429
    assert (
        client.post("/auth/login", json={"username": "ghost", "password": "x"}).status_code == 429
    )


def test_sessions_end_on_logout_disable_password_reset_and_delete(client):
    session = member(client)
    assert client.post("/admin/logout", headers=session).json() == {"signed_out": True}
    assert client.get("/admin/me", headers=session).status_code == 401

    session = sign_in(client)
    client.patch("/admin/users/dev", headers=ADMIN, json={"disabled": True})
    assert client.get("/admin/me", headers=session).status_code == 401
    assert (
        client.post("/auth/login", json={"username": "dev", "password": PASSWORD}).status_code
        == 401
    )
    client.patch("/admin/users/dev", headers=ADMIN, json={"disabled": False})

    session = sign_in(client)
    reset = client.patch("/admin/users/dev", headers=ADMIN, json={"generate_password": True})
    generated = reset.json()["password"]
    assert len(generated) >= 20
    assert client.get("/admin/me", headers=session).status_code == 401
    session = sign_in(client, password=generated)

    assert client.delete("/admin/users/dev", headers=ADMIN).status_code == 200
    assert client.get("/admin/me", headers=session).status_code == 401
    assert client.get("/admin/users", headers=ADMIN).json() == []


def test_expired_session_is_rejected(client, env):
    session = member(client)
    with sqlite3.connect(env / "state.sqlite3") as conn:
        conn.execute("UPDATE sessions SET expires_at = datetime('now', '-1 minute')")
    response = client.get("/admin/me", headers=session)
    assert response.status_code == 401 and "sign in again" in response.text
    client.app.state.store.purge_expired_sessions()
    with sqlite3.connect(env / "state.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_member_changes_own_password_and_other_sessions_end(client):
    first = member(client)
    second = sign_in(client)
    body = {"current_password": PASSWORD, "new_password": "another-long-password"}
    assert client.post(
        "/admin/me/password", headers=first, json={**body, "current_password": "bad"}
    )
    assert client.post("/admin/me/password", headers=first, json=body).json() == {"changed": True}
    assert client.get("/admin/me", headers=first).status_code == 200
    assert client.get("/admin/me", headers=second).status_code == 401
    sign_in(client, password="another-long-password")
    assert client.post("/admin/me/password", headers=ADMIN, json=body).status_code == 409


def test_member_management_validates_and_never_stores_or_returns_passwords(client, env):
    assert (
        client.post(
            "/admin/users", headers=ADMIN, json={"username": "Bad Name", "password": PASSWORD}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/users", headers=ADMIN, json={"username": "dev", "password": "short"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/users",
            headers=ADMIN,
            json={"username": "dev", "password": PASSWORD, "apps": ["nope"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/users", headers=ADMIN, json={"username": "admin-token", "password": PASSWORD}
        ).status_code
        == 422
    )
    created = client.post(
        "/admin/users",
        headers=ADMIN,
        json={"username": "dev", "generate_password": True, "apps": ["mine"]},
    ).json()
    assert created["apps"] == ["mine"] and len(created["password"]) >= 20
    assert (
        client.post(
            "/admin/users", headers=ADMIN, json={"username": "dev", "password": PASSWORD}
        ).status_code
        == 409
    )
    listed = client.get("/admin/users", headers=ADMIN).json()
    assert listed[0]["username"] == "dev" and listed[0]["apps"] == ["mine"]
    assert "password" not in str(listed) and created["password"] not in str(listed)
    with sqlite3.connect(env / "state.sqlite3") as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("scrypt$") and created["password"] not in stored
    audit = client.get("/admin/audit", headers=ADMIN).text
    assert "member.create" in audit and created["password"] not in audit
    assert client.patch("/admin/users/ghost", headers=ADMIN, json={"apps": []}).status_code == 404


def test_removed_app_never_grants_access_again_when_its_name_is_reused(client):
    session = member(client, apps=("mine", "other"))
    assert client.delete("/admin/apps/other", headers=ADMIN).status_code == 200
    assert set(client.get("/admin/apps", headers=session).json()) == {"mine"}
    spec = client.get("/admin/apps", headers=ADMIN).json()["mine"]
    spec = {key: value for key, value in spec.items() if key not in ("secret", "github")}
    assert client.put("/admin/apps/other", headers=ADMIN, json=spec).status_code == 200
    assert set(client.get("/admin/apps", headers=session).json()) == {"mine"}
    assert client.get("/admin/users", headers=ADMIN).json()[0]["apps"] == ["mine"]


def test_admin_token_still_works_and_a_wrong_one_is_not_rescued_by_a_session(client):
    session = member(client)
    assert set(client.get("/admin/apps", headers=ADMIN).json()) == {"mine", "other"}
    mixed = {**session, "X-Admin-Token": "wrong" * 8}
    assert client.get("/admin/apps", headers=mixed).status_code == 401
    assert client.get("/admin/me", headers=ADMIN).json()["role"] == "admin"


def test_password_hashing_round_trip():
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password(PASSWORD + "x", stored)
    assert not auth.verify_password(PASSWORD, "scrypt$broken")
    assert auth.hash_password(PASSWORD) != stored


def test_existing_database_gains_member_tables_and_audit_app_column(tmp_path):
    from deployd.store import db as db_module

    path = tmp_path / "v2.sqlite3"
    previous = db_module._MIGRATIONS[:2]
    original, version = db_module._MIGRATIONS, db_module.SCHEMA_VERSION
    db_module._MIGRATIONS, db_module.SCHEMA_VERSION = previous, 2
    try:
        store = Store(path)
        store.init()
        did = store.create_deploy("site", "a" * 40, "https://example.com/a", "b" * 64, "t")
        with sqlite3.connect(path) as conn:
            conn.executemany(
                "INSERT INTO audit_log (actor, action, target) VALUES ('dan', ?, ?)",
                [
                    ("app.freeze", "site"),
                    ("deploy.cancel", did),
                    ("account.password.change", "dan"),
                ],
            )
    finally:
        db_module._MIGRATIONS, db_module.SCHEMA_VERSION = original, version
    Store(path).init()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db_module.SCHEMA_VERSION
        assert [row[0] for row in conn.execute("SELECT app FROM audit_log ORDER BY id")] == [
            "site",
            "site",
            None,
        ]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert {"users", "user_apps", "sessions"} <= tables
