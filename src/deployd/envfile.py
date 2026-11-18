"""Client for the root .env helper. File contents pass through and are never logged."""

import asyncio
import json
import subprocess
from pathlib import Path

HELPER = Path("/usr/local/libexec/deployd/env-file")


class EnvFileError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _invoke(operation, name, spec, payload=None):
    if spec.env_file is None:
        raise EnvFileError(409, "no .env file is configured for this app")
    if not HELPER.is_file():
        raise EnvFileError(503, "Run the installer and enable the .env editor first.")
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", str(HELPER), operation, name, str(spec.env_file)],
            input=json.dumps(payload).encode() if payload is not None else b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=40,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        data = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise EnvFileError(
            503, "The .env helper did not answer. Rerun the installer to repair it."
        ) from None
    if not isinstance(data, dict):
        raise EnvFileError(503, "The .env helper returned an unexpected answer.")
    if "error" in data:
        raise EnvFileError(409, str(data["error"]))
    return data


async def call(operation, name, spec, payload=None):
    return await asyncio.to_thread(_invoke, operation, name, spec, payload)
