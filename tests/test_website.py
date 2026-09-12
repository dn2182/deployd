from pathlib import Path

import pytest

from deployd.config import AppSpec
from deployd.store.db import Store
from deployd.worker import website


@pytest.fixture
def spec(monkeypatch, tmp_path):
    monkeypatch.setattr(website.sys, "platform", "linux")
    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(website, "HELPER", helper)
    return AppSpec.model_validate(
        {
            "releases_dir": "/srv/deployd/site/releases",
            "current_link": "/srv/deployd/site/releases/current",
            "site_path": "/var/www/example.com",
            "release_layout": "directory",
            "artifact": {"allowed_url_prefix": "https://example.com/"},
            "restart": {
                "command": ["/usr/bin/test", "-s", "/srv/deployd/site/releases/current/index.html"]
            },
        }
    )


def test_fixed_helper_command(spec):
    assert website.arguments("site", spec, "connect") == [
        "/usr/bin/sudo",
        "-n",
        str(website.HELPER),
        "connect",
        "site",
        "example.com",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("site_path", Path("/etc")),
        ("site_path", Path("/var/www/nested/site")),
        ("release_layout", "symlink"),
        ("releases_dir", Path("/tmp/releases")),
        ("current_link", Path("/tmp/current")),
    ],
)
def test_rejects_nonstandard_connection_paths(spec, field, value):
    setattr(spec, field, value)
    with pytest.raises(ValueError, match="managed static site"):
        website.arguments("site", spec, "connect")


async def test_connection_failure_is_reported_without_claiming_success(spec, monkeypatch, tmp_path):
    store = Store(tmp_path / "store.sqlite3")
    store.init()
    did = store.create_deploy("site", "0" * 40, "local-website://connect", "0" * 64, "connect:site")
    monkeypatch.setattr(website, "get_app_registry", lambda: {"site": spec})

    async def denied(*args):
        raise RuntimeError("original saved; retry connection to finish recovery")

    monkeypatch.setattr(website.runner, "_run_cmd", denied)
    await website.run_connection(store, "site", did)
    assert store.get_deploy(did)["status"] == "failed"
