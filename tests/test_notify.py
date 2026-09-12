import httpx

from deployd import notify
from deployd.config import AppSpec

DEPLOY = {
    "deploy_id": "d" * 32,
    "app": "app-x",
    "commit_sha": "a" * 40,
    "triggered_by": "ci",
    "kind": "artifact",
}


def _spec(tmp_path, **notify_spec):
    return AppSpec.model_validate(
        {
            "releases_dir": str(tmp_path / "releases"),
            "current_link": str(tmp_path / "current"),
            "artifact": {"allowed_url_prefix": "https://example.com/"},
            "restart": {"command": ["true"]},
            "notify": notify_spec,
        }
    )


def _capture(monkeypatch, responses):
    calls = []

    def handler(request):
        calls.append(request)
        return responses.pop(0)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        notify.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(notify, "BACKOFF_SECONDS", 0)
    return calls


def test_payload_formats():
    spec = _spec.__wrapped__ if hasattr(_spec, "__wrapped__") else None
    assert spec is None
    generic = notify.build_payload(
        AppSpec.model_validate(
            {
                "releases_dir": "/srv/x/releases",
                "current_link": "/srv/x/current",
                "artifact": {"allowed_url_prefix": "https://example.com/"},
                "restart": {"command": ["true"]},
            }
        ).notify,
        DEPLOY,
        "failed",
        "migrate",
    )
    assert generic["event"] == "failed" and generic["failed_step"] == "migrate"
    assert generic["summary"].startswith("deployd: app-x failed @ aaaaaaaaaaaa")
    slack = notify.build_payload(
        AppSpec.model_validate(
            {
                "releases_dir": "/srv/x/releases",
                "current_link": "/srv/x/current",
                "artifact": {"allowed_url_prefix": "https://example.com/"},
                "restart": {"command": ["true"]},
                "notify": {"format": "slack"},
            }
        ).notify,
        DEPLOY,
        "rolled_back",
        None,
    )
    assert set(slack) == {"text"}


async def test_only_selected_events_are_sent(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [httpx.Response(200)])
    spec = _spec(tmp_path, url="https://hooks.example.com/x", events=["failed"])
    await notify.send(spec, DEPLOY, "succeeded")
    assert calls == []
    await notify.send(spec, DEPLOY, "failed", "health")
    assert len(calls) == 1
    assert calls[0].url == "https://hooks.example.com/x"
    assert (
        b'"failed_step": "health"' in calls[0].content
        or b'"failed_step":"health"' in calls[0].content
    )


async def test_no_url_means_no_request(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [])
    await notify.send(_spec(tmp_path), DEPLOY, "failed")
    assert calls == []


async def test_server_errors_are_retried_then_logged(tmp_path, monkeypatch, caplog):
    calls = _capture(monkeypatch, [httpx.Response(503), httpx.Response(503), httpx.Response(503)])
    spec = _spec(tmp_path, url="https://hooks.example.com/x", events=["failed"])
    await notify.send(spec, DEPLOY, "failed", "restart")
    assert len(calls) == 3
    assert "failed after 3 attempts" in caplog.text


async def test_client_errors_are_not_retried(tmp_path, monkeypatch, caplog):
    calls = _capture(monkeypatch, [httpx.Response(400)])
    spec = _spec(tmp_path, url="https://hooks.example.com/x", events=["failed"])
    await notify.send(spec, DEPLOY, "failed", "restart")
    assert len(calls) == 1
    assert "rejected with HTTP 400" in caplog.text
