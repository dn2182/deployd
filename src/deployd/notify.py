"""Outbound deploy notifications. Failures are logged, never raised into the pipeline."""

import asyncio
import logging
import socket

import httpx

from .config import AppSpec, NotifySpec

log = logging.getLogger("deployd.notify")

_ATTEMPTS = 3
BACKOFF_SECONDS = 2
_TIMEOUT = httpx.Timeout(10)


def build_payload(notify: NotifySpec, deploy: dict, status: str, failed_step: str | None) -> dict:
    sha = deploy["commit_sha"]
    summary = f"deployd: {deploy['app']} {status} @ {sha[:12]} on {socket.gethostname()}"
    if failed_step:
        summary += f" (step: {failed_step})"
    if notify.format == "slack":
        return {"text": summary}
    if notify.format == "discord":
        return {"content": summary}
    return {
        "event": status,
        "app": deploy["app"],
        "deploy_id": deploy["deploy_id"],
        "commit_sha": sha,
        "kind": deploy.get("kind", "artifact"),
        "triggered_by": deploy["triggered_by"],
        "failed_step": failed_step,
        "host": socket.gethostname(),
        "summary": summary,
    }


async def send(spec: AppSpec, deploy: dict, status: str, failed_step: str | None = None) -> None:
    notify = spec.notify
    if not notify.url or status not in notify.events:
        return
    try:
        await _deliver(notify.url, build_payload(notify, deploy, status, failed_step), deploy)
    except Exception:
        log.warning("notification for %s could not be sent", deploy["deploy_id"], exc_info=True)


async def _deliver(url: str, payload: dict, deploy: dict) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await client.post(url, json=payload)
                if response.status_code < 500 and response.status_code != 429:
                    if response.status_code >= 400:
                        log.warning(
                            "notification for %s rejected with HTTP %s",
                            deploy["deploy_id"],
                            response.status_code,
                        )
                    return
                reason = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                reason = exc.__class__.__name__
            if attempt < _ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS * attempt)
        log.warning(
            "notification for %s failed after %s attempts: %s",
            deploy["deploy_id"],
            _ATTEMPTS,
            reason,
        )
