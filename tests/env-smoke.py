"""Exercise the .env editor through Nginx TLS, the API and the helper inside the service sandbox."""

import base64
import grp
import json
import os
import ssl
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
insecure = ssl.create_default_context()
insecure.check_hostname = False
insecure.verify_mode = ssl.CERT_NONE


def request(path, body=None, method=None):
    auth = base64.b64encode(b"smoke-admin:smoke-password").decode()
    req = urllib.request.Request(
        f"https://127.0.0.1:844/api{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Basic {auth}",
            "X-Admin-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60, context=insecure) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


folder = Path("/etc/deployd-smoke")
env_file = folder / "app.env"
assert not folder.exists()
folder.mkdir(mode=0o750)
env_file.write_text("DB_PASSWORD=original\n")
os.chown(env_file, 0, grp.getgrnam("www-data").gr_gid)
env_file.chmod(0o640)

spec = {
    "env_file": str(env_file),
    "releases_dir": "/srv/deployd/env-smoke/releases",
    "current_link": "/srv/deployd/env-smoke/current",
    "artifact": {"allowed_url_prefix": "https://example.com/"},
    "restart": {"command": ["/usr/bin/true"]},
}
for forbidden in ("/etc/shadow", "/opt/deployd/.env", "/var/lib/deployd/secrets.env"):
    assert request("/admin/apps/env-smoke", {**spec, "env_file": forbidden}, "PUT")[0] == 422
assert request("/admin/apps/env-smoke", spec, "PUT")[0] == 200

status, info = request("/admin/apps/env-smoke/env")
assert status == 200 and info["available"] and info["exists"], info
assert (info["owner"], info["group"], info["mode"], info["backups"]) == (
    "root",
    "www-data",
    "0640",
    0,
), info
status, loaded = request("/admin/apps/env-smoke/env/content")
assert status == 200 and loaded["content"] == "DB_PASSWORD=original\n"

status, bad = request("/admin/apps/env-smoke/env", {"content": "not valid\n"}, "PUT")
assert status == 409 and "line 1" in bad["detail"], bad
status, stale = request(
    "/admin/apps/env-smoke/env", {"content": "A=1\n", "expected_sha256": "0" * 64}, "PUT"
)
assert status == 409 and "reload" in stale["detail"], stale
assert env_file.read_text() == "DB_PASSWORD=original\n"

status, saved = request(
    "/admin/apps/env-smoke/env",
    {
        "content": "DB_PASSWORD=changed\nNEW=1\n",
        "expected_sha256": loaded["sha256"],
        "restart": True,
    },
    "PUT",
)
assert status == 200 and saved["saved"] and saved["deploy_id"], saved
assert (saved["changed"], saved["added"]) == (["DB_PASSWORD"], ["NEW"])
assert env_file.read_text() == "DB_PASSWORD=changed\nNEW=1\n"
metadata = env_file.stat()
assert (metadata.st_uid, metadata.st_mode & 0o777) == (0, 0o640)
assert grp.getgrgid(metadata.st_gid).gr_name == "www-data"
assert [path.name for path in folder.iterdir()] == ["app.env"]
kept = list(Path("/var/lib/deployd-env/env-smoke").rglob("*.env"))
assert len(kept) == 1 and kept[0].read_text() == "DB_PASSWORD=original\n"
assert kept[0].stat().st_mode & 0o777 == 0o600

for _ in range(60):
    status, deploy = request(f"/deploys/{saved['deploy_id']}")
    if deploy["status"] not in ("queued", "running"):
        break
    time.sleep(1)
assert deploy["status"] == "succeeded", deploy

status, restored = request("/admin/apps/env-smoke/env/restore", {"confirm": "env-smoke"}, "POST")
assert status == 200 and restored["saved"], restored
assert env_file.read_text() == "DB_PASSWORD=original\n"

status, audit = request("/admin/audit")
actions = [row["action"] for row in audit]
assert {"env.read", "env.write", "env.restore"} <= set(actions), actions
assert "original" not in json.dumps(audit)

assert request("/admin/apps/env-smoke", method="DELETE")[0] == 200
env_file.unlink()
folder.rmdir()
print("Env editor read, validation, conflict, write, restart, restore and audit passed.")
