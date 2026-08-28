"""Contract: X-Deploy-Signature: sha256=<hex HMAC-SHA256(secret, "{timestamp}.{raw_body}")>."""

import hashlib
import hmac
import time

from .config import get_settings


class AuthError(Exception):
    pass


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    msg = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_request(
    secret: str,
    timestamp: str,
    nonce: str,
    signature_header: str,
    body: bytes,
    *,
    seen_nonce: bool,
) -> None:
    """Raises AuthError unless everything checks out. Caller records the nonce
    only AFTER this passes, so a failed request can't poison the nonce store."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthError("bad timestamp") from exc

    window = get_settings().timestamp_window_seconds
    if abs(time.time() - ts) > window:
        raise AuthError("timestamp outside window")

    if not nonce or len(nonce) > 64:
        raise AuthError("bad nonce")
    if seen_nonce:
        raise AuthError("nonce replayed")

    if not signature_header or not signature_header.startswith("sha256="):
        raise AuthError("bad signature header")
    expected = compute_signature(secret, timestamp, body)
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise AuthError("signature mismatch")
