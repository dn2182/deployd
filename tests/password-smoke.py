"""Exercise the installed password helper through Nginx and the sandboxed service."""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

assert os.environ.get("GITHUB_ACTIONS") == "true" and os.geteuid() == 0
token = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in Path("/opt/deployd/.env").read_text().splitlines()
    if line.startswith("DEPLOYD_ADMIN_TOKEN=")
)


def request(path, password, body=None):
    auth = base64.b64encode(f"smoke-admin:{password}".encode()).decode()
    req = urllib.request.Request(
        f"http://127.0.0.1:844{path}",
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Basic {auth}",
            "X-Admin-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


status, body = request("/api/admin/account", "smoke-password")
assert status == 200 and json.loads(body)["password_change_available"]
status, _ = request(
    "/api/admin/account/password",
    "smoke-password",
    {"current_password": "smoke-password", "new_password": "replacement-smoke-password"},
)
assert status == 200
assert request("/api/healthz", "smoke-password")[0] == 401
assert request("/api/healthz", "replacement-smoke-password")[0] == 200
time.sleep(5)
assert (
    request(
        "/api/admin/account/password",
        "replacement-smoke-password",
        {"current_password": "replacement-smoke-password", "new_password": "smoke-password"},
    )[0]
    == 200
)
assert request("/api/healthz", "smoke-password")[0] == 200
print("Management password change and reauthentication passed.")
