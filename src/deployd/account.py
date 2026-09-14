import json
import subprocess
import threading
import time
from pathlib import Path

HELPER = Path("/usr/local/libexec/deployd/change-password")
_lock = threading.Lock()
_next_attempt = 0.0


class PasswordError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def change_password(username, current, new):
    global _next_attempt
    if not HELPER.is_file():
        raise PasswordError(503, "Run the updated installer to enable password changes.")
    if not _lock.acquire(blocking=False):
        raise PasswordError(429, "A password change is already in progress.")
    try:
        if time.monotonic() < _next_attempt:
            raise PasswordError(429, "Wait a few seconds before trying again.")
        _next_attempt = time.monotonic() + 5
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-n", str(HELPER)],
                input=json.dumps(
                    {"username": username, "current_password": current, "new_password": new}
                ).encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PasswordError(
                503,
                "Password change could not be confirmed. Try signing in with the new password before retrying.",
            ) from None
        if result.returncode == 3:
            raise PasswordError(400, "Current password is incorrect.")
        if result.returncode:
            raise PasswordError(
                503, "Password change unavailable. Check the management helper installation."
            )
    finally:
        _lock.release()
