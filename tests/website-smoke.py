"""Installed-helper integration, restricted to a disposable GitHub Ubuntu runner."""

import configparser
import json
import os
import pwd
import subprocess
from pathlib import Path

if os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true":
    raise SystemExit("requires the disposable installer CI runner")

site = Path("/var/www/deployd-smoke.example")
releases = Path("/srv/deployd/website-smoke/releases")
assert not os.path.lexists(site) and not os.path.lexists(releases.parent)
site.mkdir(mode=0o755)
(site / "index.html").write_text("original site")
(site / "index.html").chmod(0o644)
current = releases / "current"
current.mkdir(parents=True, mode=0o755)
(current / "index.html").write_text("deployed site")
(current / "index.html").chmod(0o644)
(current / ".deployd-release.json").write_text(
    json.dumps(
        {
            "name": "a" * 40 + "-" + "b" * 32,
            "previous": None,
            "created_at": 1,
        }
    )
)
(current / ".deployd-release.json").chmod(0o600)
account = pwd.getpwnam("deployd")
for path in [releases.parent, releases, current, *current.iterdir()]:
    os.chown(path, account.pw_uid, account.pw_gid)
    if path.is_dir():
        path.chmod(0o755)

unit = configparser.ConfigParser(interpolation=None)
unit.read("/etc/systemd/system/deployd.service")
properties = [
    "User",
    "Group",
    "NoNewPrivileges",
    "ProtectSystem",
    "ProtectHome",
    "PrivateTmp",
    "PrivateDevices",
    "ProtectHostname",
    "ProtectClock",
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectKernelLogs",
    "ProtectControlGroups",
    "ProtectProc",
    "ProcSubset",
    "RestrictNamespaces",
    "RestrictRealtime",
    "RestrictSUIDSGID",
    "LockPersonality",
    "RestrictAddressFamilies",
    "SystemCallArchitectures",
    "UMask",
]
command = ["systemd-run", "--wait", "--pipe", "--collect"]
for key in properties:
    if key in unit["Service"]:
        command += ["--property", f"{key}={unit['Service'][key]}"]
command += ["/usr/bin/sudo", "-n", "/usr/local/libexec/deployd/connect-website"]
subprocess.run([*command, "check", "website-smoke", site.name], check=True)
assert not site.is_symlink()
subprocess.run([*command, "connect", "website-smoke", site.name], check=True)
assert site.is_symlink() and site.resolve() == current
assert (releases / "b4deployd/index.html").read_text() == "original site"
subprocess.run([*command, "connect", "website-smoke", site.name], check=True)
subprocess.run(["sudo", "-u", "www-data", "test", "-r", str(site / "index.html")], check=True)
assert (releases / "b4deployd").stat().st_uid == account.pw_uid
assert subprocess.run([*command, "connect", "website-smoke", "../../etc"]).returncode != 0
print("Installed helper, service sandbox, repeat connection and Nginx access passed.")
