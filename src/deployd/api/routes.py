import hashlib

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import config_lock, get_app_registry, get_app_secret, get_settings
from ..models import DeployAccepted, DeployDetail, DeployRequest
from ..security import AuthError, signed_message, verify_request

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


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

    registry = get_app_registry()
    if payload.app not in registry:
        raise HTTPException(status_code=404, detail="unknown app")
    secret = get_app_secret(payload.app)
    if not secret:
        raise HTTPException(status_code=503, detail="app secret not configured")
    if len(secret.encode()) < 32:
        raise HTTPException(status_code=503, detail="app secret must contain at least 32 bytes")

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
    with config_lock():
        current_registry = get_app_registry()
        if payload.app not in current_registry:
            raise HTTPException(status_code=409, detail="app was removed during request")
        if get_app_secret(payload.app) != secret:
            raise HTTPException(status_code=401, detail="app secret changed during request")
        if not current_registry[payload.app].artifact.allows_initial_url(str(payload.artifact_url)):
            raise HTTPException(status_code=403, detail="artifact rules changed during request")
        result = store.create_deploy_once(
            x_deploy_nonce,
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
        deploy_status = store.get_deploy(deploy_id)["status"]
        if created or deploy_status == "queued":
            request.app.state.queue.enqueue(payload.app, deploy_id)
    return DeployAccepted(deploy_id=deploy_id, status=deploy_status)


@router.get("/deploys/{deploy_id}", response_model=DeployDetail)
async def get_deploy(request: Request, deploy_id: str):
    row = request.app.state.store.get_deploy(deploy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown deploy")
    return DeployDetail(
        deploy_id=row["deploy_id"],
        app=row["app"],
        commit_sha=row["commit_sha"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        steps=row["steps"],
    )
