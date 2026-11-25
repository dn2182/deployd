"""Team members through the real Nginx site: session login beside the owner's Basic Auth."""

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true" and os.geteuid() == 0
token = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in Path("/opt/deployd/.env").read_text().splitlines()
    if line.startswith("DEPLOYD_ADMIN_TOKEN=")
)
insecure = ssl.create_default_context()
insecure.check_hostname = False
insecure.verify_mode = ssl.CERT_NONE
BASIC = "Basic " + base64.b64encode(b"smoke-admin:smoke-password").decode()
OWNER = {"Authorization": BASIC, "X-Admin-Token": token}
PASSWORD = "members-smoke-password"


def request(path, headers=None, body=None, method=None):
    req = urllib.request.Request(
        f"https://127.0.0.1:844{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=insecure) as response:
            return response.status, json.loads(response.read() or b"null"), response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), exc.headers
        except ValueError:
            return exc.code, None, exc.headers


def basic_challenge(headers):
    return (headers.get("WWW-Authenticate") or "").startswith("Basic")


spec = {
    "releases_dir": "/srv/deployd/members-a/releases",
    "current_link": "/srv/deployd/members-a/current",
    "artifact": {"allowed_url_prefix": "https://example.com/"},
    "restart": {"command": ["/usr/bin/true"]},
}
for name in ("members-a", "members-b"):
    renamed = json.loads(json.dumps(spec).replace("members-a", name))
    assert request(f"/api/admin/apps/{name}", OWNER, renamed, "PUT")[0] == 200

# The page and the health check load without credentials; the API does not.
with urllib.request.urlopen("https://127.0.0.1:844/", timeout=30, context=insecure) as page:
    assert page.status == 200
assert request("/api/healthz")[0] == 200
status, _, headers = request("/api/admin/apps")
assert status == 401 and basic_challenge(headers)

# The admin token never gets past Basic Auth, with or without a session header beside it.
for extra in ({}, {"X-Deployd-Session": "anything"}):
    status, _, headers = request("/api/admin/apps", {"X-Admin-Token": token, **extra})
    assert status == 401 and basic_challenge(headers), (status, extra)

status, created, _ = request(
    "/api/admin/users",
    OWNER,
    {"username": "smoke-dev", "password": PASSWORD, "apps": ["members-a"]},
)
assert status == 201, created
status, _, headers = request("/api/admin/apps", {"X-Deployd-Session": "not-a-session"})
assert status == 401 and not basic_challenge(headers)
status, wrong, _ = request("/api/auth/login", body={"username": "smoke-dev", "password": "x" * 12})
assert status == 401, wrong
status, session, _ = request(
    "/api/auth/login", body={"username": "smoke-dev", "password": PASSWORD}
)
assert status == 200 and session["token"], session
MEMBER = {"X-Deployd-Session": session["token"]}

status, apps, _ = request("/api/admin/apps", MEMBER)
assert status == 200 and list(apps) == ["members-a"], apps
assert request("/api/admin/apps/members-a/status", MEMBER)[0] == 200
assert request("/api/admin/apps/members-b/status", MEMBER)[0] == 404
assert request("/api/admin/users", MEMBER)[0] == 403
assert request("/api/admin/apps/members-a", MEMBER, method="DELETE")[0] == 403
# A Basic Auth name sent by a member is never taken as who they are.
forged = {**MEMBER, "Authorization": "Basic " + base64.b64encode(b"root:x").decode()}
assert request("/api/admin/apps/members-a/freeze", forged, {"frozen": True})[0] == 200
status, audit, _ = request("/api/admin/audit", OWNER)
assert [row["actor"] for row in audit if row["action"] == "app.freeze"][0] == "smoke-dev"

assert request("/api/admin/logout", MEMBER, method="POST")[0] == 200
assert request("/api/admin/apps", MEMBER)[0] == 401
assert request("/api/admin/users/smoke-dev", OWNER, method="DELETE")[0] == 200
for name in ("members-a", "members-b"):
    assert request(f"/api/admin/apps/{name}", OWNER, method="DELETE")[0] == 200
print("Member sign-in, app scoping, owner-only routes and Basic Auth for the admin token passed.")
