"""Admin surface. Own auth (X-Admin-Token) — the deploy HMAC does not cover it.
Bind to localhost/tailnet or front with Cloudflare Access; never expose bare.
"""

import hashlib
import hmac
import os
import secrets as pysecrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request

from ..config import (
    APP_NAME_PATTERN,
    AppSpec,
    _secret_key,
    config_lock,
    delete_app_config,
    get_app_registry,
    get_app_secret,
    get_settings,
    set_app_secret,
    upsert_app,
)

AppName = Annotated[str, Path(pattern=APP_NAME_PATTERN)]


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(status_code=503, detail="admin token not configured")
    if not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(status_code=401, detail="bad admin token")


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _secret_info(app_name: str) -> dict:
    secret = get_app_secret(app_name)
    return {
        "configured": secret is not None,
        "fingerprint": hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else None,
        "env_override": _secret_key(app_name) in os.environ,
    }


@router.get("/apps")
async def list_apps():
    return {
        name: {**spec.model_dump(mode="json"), "secret": _secret_info(name)}
        for name, spec in get_app_registry().items()
    }


@router.put("/apps/{name}")
async def upsert_app_route(name: AppName, spec: AppSpec):
    upsert_app(name, spec)
    return {"status": "saved", "app": name}


@router.delete("/apps/{name}")
async def delete_app(request: Request, name: AppName):
    with config_lock():
        if request.app.state.store.has_active_deploys(name):
            raise HTTPException(status_code=409, detail="app has queued or running deployments")
        if _secret_key(name) in os.environ:
            raise HTTPException(
                status_code=409,
                detail=f"unset {_secret_key(name)} from the service environment before deletion",
            )
        if not delete_app_config(name):
            raise HTTPException(status_code=404, detail="unknown app")
    return {"status": "deleted", "app": name}


@router.post("/apps/{name}/rotate-secret")
async def rotate_secret(name: AppName):
    with config_lock():
        if name not in get_app_registry():
            raise HTTPException(status_code=404, detail="unknown app")
        if _secret_key(name) in os.environ:
            raise HTTPException(
                status_code=409,
                detail="secret is managed by the service environment; rotate it there",
            )
        new_secret = pysecrets.token_hex(32)
        set_app_secret(name, new_secret)
    return {
        # shown exactly once — copy it into the CI secret now
        "secret": new_secret,
        "fingerprint": hashlib.sha256(new_secret.encode()).hexdigest()[:12],
        "env_override": False,
        "warning": None,
    }


@router.get("/deploys")
async def list_deploys(
    request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 50, app: str | None = None
):
    return request.app.state.store.list_deploys(limit=limit, app=app)


@router.post("/deploys/{deploy_id}/redeploy")
async def redeploy(request: Request, deploy_id: str):
    store = request.app.state.store
    old = store.get_deploy(deploy_id)
    if old is None:
        raise HTTPException(status_code=404, detail="unknown deploy")
    if old["app"] not in get_app_registry():
        raise HTTPException(status_code=409, detail="app no longer registered")
    new_id = store.create_deploy(
        old["app"],
        old["commit_sha"],
        old["artifact_url"],
        old["artifact_sha256"],
        f"redeploy:{deploy_id[:8]}",
    )
    request.app.state.queue.enqueue(old["app"], new_id)
    return {"deploy_id": new_id, "status": "queued"}
