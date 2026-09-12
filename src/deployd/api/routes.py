import hashlib
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import config_lock, get_app_registry, get_app_secret, get_settings
from ..models import DeployAccepted, DeployDetail, DeployRequest
from ..security import AuthError, constant_time_equal, signed_message, verify_request

router = APIRouter()
log = logging.getLogger("deployd.api")


@router.get("/healthz")
async def healthz(request: Request):
    checks = {"db": "ok", "worker": "ok"}
    try:
        request.app.state.store.check_writable()
    except Exception as exc:
        checks["db"] = exc.__class__.__name__
    if not request.app.state.queue.healthy():
        checks["worker"] = "a worker task has stopped"
    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return {"status": status, **checks}


def is_admin_request(request: Request) -> bool:
    expected = get_settings().admin_token
    return bool(expected) and constant_time_equal(expected, request.headers.get("x-admin-token"))


@router.post("/deploys", response_model=DeployAccepted, status_code=202)
async def create_deploy(
    request: Request,
    x_deploy_timestamp: str = Header(...),
    x_deploy_nonce: str = Header(...),
    x_deploy_signature: str = Header(...),
):
    declared = request.headers.get("content-length")
    limit = get_settings().max_request_bytes
    if declared is not None:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="request body too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    store = request.app.state.store

    # Parse just enough to find the app -> secret; full HMAC check before
    # trusting anything else in the payload.
    try:
        payload = DeployRequest.model_validate_json(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid payload") from exc

    # Unknown apps and unconfigured secrets answer like a bad signature so the
    # public endpoint does not enumerate the registry; the reason goes to the log.
    registry = get_app_registry()
    secret = get_app_secret(payload.app) if payload.app in registry else None
    if payload.app not in registry:
        log.warning("deploy request for unknown app %s", payload.app)
        raise HTTPException(status_code=401, detail="unauthorized")
    if not secret or len(secret.encode()) < 32:
        log.warning(
            "deploy request for %s: app secret missing or shorter than 32 bytes", payload.app
        )
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        verify_request(
            secret,
            x_deploy_timestamp,
            x_deploy_nonce,
            x_deploy_signature,
            body,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    spec = registry[payload.app]
    if not spec.artifact.allows_initial_url(str(payload.artifact_url)):
        raise HTTPException(status_code=403, detail="artifact URL not allowed for this app")
    if spec.frozen:
        raise HTTPException(status_code=423, detail="app is frozen; unfreeze it in management")
    with config_lock():
        if request.app.state.queue.is_removing(payload.app):
            raise HTTPException(status_code=409, detail="app removal is in progress")
        result = store.create_deploy_once(
            f"{payload.app}:{x_deploy_nonce}",
            hashlib.sha256(signed_message(x_deploy_timestamp, x_deploy_nonce, body)).hexdigest(),
            payload.app,
            payload.commit_sha,
            str(payload.artifact_url),
            payload.artifact_sha256,
            payload.triggered_by,
        )
        if result is None:
            raise HTTPException(status_code=401, detail="nonce replayed with different request")
        deploy_id, created = result
        deploy_status = store.get_status(deploy_id)
        if created:
            # Only the newest queued artifact for an app is worth running.
            for superseded in store.supersede_queued(payload.app, deploy_id):
                log.info("deploy %s superseded by %s", superseded, deploy_id)
        if created or deploy_status == "queued":
            request.app.state.queue.enqueue(payload.app, deploy_id)
    return DeployAccepted(deploy_id=deploy_id, status=deploy_status)


@router.get("/deploys/{deploy_id}", response_model=DeployDetail)
async def get_deploy(request: Request, deploy_id: str):
    row = request.app.state.store.get_deploy(deploy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown deploy")
    # Command output can carry connection strings and stack traces; CI polling
    # only needs step names and statuses.
    reveal_output = is_admin_request(request)
    return DeployDetail(
        deploy_id=row["deploy_id"],
        app=row["app"],
        commit_sha=row["commit_sha"],
        kind=row["kind"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        steps=[
            {**step, "output": step["output"] if reveal_output else None} for step in row["steps"]
        ],
    )
