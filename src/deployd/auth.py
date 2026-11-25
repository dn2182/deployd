"""Who is calling the management API: the owner (admin token) or a team member (session).

Members are optional accounts limited to the applications assigned to them. The
admin token stays the owner credential and is the only one that can change
configuration, since app commands run on the server.
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import get_app_registry, get_settings
from .security import constant_time_equal

SESSION_HEADER = "x-deployd-session"
SESSION_TTL_SECONDS = 12 * 3600
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,31}$"
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_BYTES = 256
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
# Names the audit log already uses for the owner.
RESERVED_USERNAMES = frozenset({"admin-token"})

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


@dataclass(frozen=True)
class Principal:
    name: str
    admin: bool
    apps: frozenset[str] | None = None
    session: str | None = None

    def can(self, app: str) -> bool:
        return self.admin or app in (self.apps or ())

    @property
    def scope(self) -> frozenset[str] | None:
        """Apps to filter lists by; None for the owner, who sees everything."""
        return None if self.admin else self.apps or frozenset()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    encode = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Spent on unknown usernames so a wrong name costs the same as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_CHARS or len(password.encode()) > MAX_PASSWORD_BYTES:
        return f"password must have at least {MIN_PASSWORD_CHARS} characters"
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        return "password cannot contain control characters"
    return None


def generate_password() -> str:
    return secrets.token_urlsafe(18)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """Counts failures per username, known or not, so lockout reveals nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}

    def blocked(self, username: str) -> bool:
        with self._lock:
            count, until = self._failures.get(username, (0, 0.0))
            return count >= MAX_FAILURES and time.monotonic() < until

    def failed(self, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            count, until = self._failures.get(username, (0, 0.0))
            if count >= MAX_FAILURES and now >= until:
                count = 0
            if len(self._failures) > 10_000:
                self._failures = {
                    name: entry for name, entry in self._failures.items() if entry[1] > now
                }
            self._failures[username] = (count + 1, now + LOCK_SECONDS)

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)


throttle = LoginThrottle()


def login(store, username: str, password: str) -> dict:
    if throttle.blocked(username):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    user = store.get_user(username)
    valid = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
    if user is None or not valid or user["disabled"]:
        throttle.failed(username)
        raise HTTPException(401, "Wrong username or password.")
    throttle.succeeded(username)
    token = secrets.token_urlsafe(32)
    expires_at = store.create_session(user["id"], token_hash(token), SESSION_TTL_SECONDS)
    return {"token": token, "expires_at": expires_at}


def _member(request: Request) -> Principal | None:
    token = request.headers.get(SESSION_HEADER)
    if not token or len(token) > 128:
        return None
    hashed = token_hash(token)
    user = request.app.state.store.session_user(hashed)
    if user is None:
        return None
    # Only applications that still exist count, whatever is stored.
    apps = user["apps"] & frozenset(get_app_registry())
    return Principal(user["username"], admin=False, apps=apps, session=hashed)


def optional_principal(request: Request) -> Principal | None:
    """The caller, or None when no valid credential was sent. Never raises."""
    provided = request.headers.get("x-admin-token")
    if provided is not None:
        expected = get_settings().admin_token
        if expected and constant_time_equal(expected, provided):
            return Principal("admin-token", admin=True)
        return None
    return _member(request)


def _reject(request: Request) -> HTTPException:
    if request.headers.get("x-admin-token") is None and request.headers.get(SESSION_HEADER):
        return HTTPException(status_code=401, detail="session expired; sign in again")
    if not get_settings().admin_token:
        return HTTPException(status_code=503, detail="admin token not configured")
    return HTTPException(status_code=401, detail="bad admin token")


def require_principal(request: Request) -> Principal:
    principal = optional_principal(request)
    if principal is None:
        raise _reject(request)
    request.state.principal = principal
    return principal


def require_admin(request: Request) -> Principal:
    principal = require_principal(request)
    if not principal.admin:
        raise HTTPException(status_code=403, detail="only the administrator can do this")
    return principal


def require_app_access(request: Request, name: str) -> Principal:
    """Members get the same answer for an app they cannot use and one that does not exist."""
    principal = require_principal(request)
    if not principal.can(name):
        raise HTTPException(status_code=404, detail="unknown app")
    return principal
