"""The vendored CI script must sign exactly like the server verifies."""

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

from deployd.security import compute_signature

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("notify_deploy", ROOT / "examples/notify_deploy.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def test_signature_parity():
    secret, ts, nonce, body = "abc", "1756100000", "nonce-1", b'{"app":"x"}'
    assert notify.sign(secret, ts, nonce, body) == compute_signature(secret, ts, nonce, body)


def test_example_matches_template():
    template = ROOT / "src/deployd/templates/notify_deploy.py.txt"
    assert (ROOT / "examples/notify_deploy.py").read_bytes() == template.read_bytes()


def test_terminal_statuses_include_superseded_and_cancelled():
    assert {"superseded", "cancelled", "failed", "rolled_back", "succeeded"} <= set(notify.TERMINAL)
    assert notify.SUCCESS == "succeeded"


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"upstream"))


@pytest.mark.parametrize("code", [429, 502, 503, 504, 520, 524])
def test_transient_statuses_are_retried_with_backoff(monkeypatch, code):
    calls = []
    sleeps = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise _http_error(code)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(notify.time, "sleep", sleeps.append)
    assert notify.request_with_retries("https://x/deploys/1") == {"ok": True}
    assert len(calls) == 3 and sleeps == [1, 2]


def test_retries_stop_after_five_attempts(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        notify.urllib.request,
        "urlopen",
        lambda req, timeout: attempts.append(1) or (_ for _ in ()).throw(_http_error(503)),
    )
    monkeypatch.setattr(notify.time, "sleep", lambda _: None)
    with pytest.raises(notify.DeploydHTTPError, match="503"):
        notify.request_with_retries("https://x/deploys/1")
    assert len(attempts) == notify.ATTEMPTS


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 500])
def test_permanent_statuses_are_not_retried(monkeypatch, code):
    attempts = []
    monkeypatch.setattr(
        notify.urllib.request,
        "urlopen",
        lambda req, timeout: attempts.append(1) or (_ for _ in ()).throw(_http_error(code)),
    )
    monkeypatch.setattr(notify.time, "sleep", lambda _: pytest.fail("must not sleep"))
    with pytest.raises(notify.DeploydHTTPError):
        notify.request_with_retries("https://x/deploys/1")
    assert len(attempts) == 1
