"""HMAC-SHA256 contract: ``timestamp.nonce.raw_body``."""

import hashlib
import hmac
import re
import time

from .config import get_settings

_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AuthError(Exception):
    pass


def compute_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), signed_message(timestamp, nonce, body), hashlib.sha256
    ).hexdigest()


def signed_message(timestamp: str, nonce: str, body: bytes) -> bytes:
    return timestamp.encode() + b"." + nonce.encode() + b"." + body


def verify_request(
    secret: str,
    timestamp: str,
    nonce: str,
    signature_header: str,
    body: bytes,
) -> None:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthError("bad timestamp") from exc

    window = get_settings().timestamp_window_seconds
    if abs(time.time() - ts) > window:
        raise AuthError("timestamp outside window")

    if not _NONCE_RE.fullmatch(nonce):
        raise AuthError("bad nonce")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature_header or ""):
        raise AuthError("bad signature header")
    expected = compute_signature(secret, timestamp, nonce, body)
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise AuthError("signature mismatch")
