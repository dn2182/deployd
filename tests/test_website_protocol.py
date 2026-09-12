from unittest.mock import AsyncMock

import pytest

from deployd.worker import runner, website


@pytest.mark.asyncio
async def test_helper_json_error_is_readable(monkeypatch):
    monkeypatch.setattr(website, "arguments", lambda *args: ["helper"])
    monkeypatch.setattr(
        runner,
        "_run_cmd",
        AsyncMock(
            side_effect=RuntimeError(
                'configured command exited 1\n{"error":"/var/www has unsafe permissions 2775",'
                '"recovery":"/var/lib/deployd-connect"}\n'
            )
        ),
    )
    with pytest.raises(ValueError, match="^/var/www has unsafe permissions 2775$"):
        await website.inspect("site", None)


@pytest.mark.asyncio
async def test_non_protocol_errors_are_preserved(monkeypatch):
    monkeypatch.setattr(website, "arguments", lambda *args: ["helper"])
    monkeypatch.setattr(
        runner, "_run_cmd", AsyncMock(side_effect=RuntimeError("sudo: password required"))
    )
    with pytest.raises(RuntimeError, match="sudo: password required"):
        await website.inspect("site", None)
