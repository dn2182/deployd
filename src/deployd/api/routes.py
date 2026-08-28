from fastapi import APIRouter, Header, HTTPException, Request

from ..config import get_app_registry, get_app_secret
from ..models import DeployAccepted, DeployDetail, DeployRequest
from ..security import AuthError, verify_request

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
    body = await request.body()
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

    try:
        verify_request(
            secret,
            x_deploy_timestamp,
            x_deploy_nonce,
            x_deploy_signature,
            body,
            seen_nonce=store.nonce_seen(x_deploy_nonce),
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    store.record_nonce(x_deploy_nonce)

    spec = registry[payload.app]
    if not str(payload.artifact_url).startswith(spec.artifact.allowed_url_prefix):
        raise HTTPException(status_code=403, detail="artifact URL not allowed for this app")

    deploy_id = store.create_deploy(
        payload.app,
        payload.commit_sha,
        str(payload.artifact_url),
        payload.artifact_sha256,
        payload.triggered_by,
    )
    request.app.state.queue.enqueue(payload.app, deploy_id)
    return DeployAccepted(deploy_id=deploy_id)


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
