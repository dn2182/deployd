import time

import pytest

from deployd.security import AuthError, compute_signature, verify_request

SECRET = "test-secret"
BODY = b'{"app":"example-api"}'


def _sign(ts: str, nonce: str = "n", body: bytes = BODY, secret: str = SECRET) -> str:
    return "sha256=" + compute_signature(secret, ts, nonce, body)


def test_valid_signature_passes():
    ts = str(int(time.time()))
    verify_request(SECRET, ts, "n", _sign(ts), BODY)


def test_wrong_secret_rejected():
    ts = str(int(time.time()))
    with pytest.raises(AuthError, match="mismatch"):
        verify_request(SECRET, ts, "n", _sign(ts, secret="other"), BODY)


def test_tampered_body_rejected():
    ts = str(int(time.time()))
    with pytest.raises(AuthError, match="mismatch"):
        verify_request(SECRET, ts, "n", _sign(ts), b'{"app":"evil"}')


def test_stale_timestamp_rejected():
    ts = str(int(time.time()) - 3600)
    with pytest.raises(AuthError, match="window"):
        verify_request(SECRET, ts, "n", _sign(ts), BODY)


def test_changed_nonce_invalidates_signature():
    ts = str(int(time.time()))
    with pytest.raises(AuthError, match="mismatch"):
        verify_request(SECRET, ts, "different", _sign(ts, nonce="original"), BODY)


def test_garbage_timestamp_rejected():
    with pytest.raises(AuthError, match="timestamp"):
        verify_request(SECRET, "not-a-number", "n", "sha256=00", BODY)
