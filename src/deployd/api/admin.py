"""Admin surface. Own auth (X-Admin-Token) — the deploy HMAC does not cover it.
Bind to localhost/tailnet or front with Cloudflare Access; never expose bare.
"""

import hashlib
import os
import re
import secrets as pysecrets
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from ..config import (
    APP_NAME_PATTERN,
    AppSpec,
    _secret_key,
    config_lock,
    configure_app,
    delete_app_config,
    get_app_registry,
    get_app_secret,
    get_settings,
    github_secret_key,
    github_token_info,
    set_app_secret,
    set_frozen,
    upsert_app,
)
from ..github_actions import GitHubActionsSettings, setup_bundle
from ..models import DeployStatus
from ..security import constant_time_equal
from ..worker import runner, website

_ACTOR_RE = re.compile(r"^[A-Za-z0-9@._-]{1,64}$")


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(status_code=503, detail="admin token not configured")
    if not constant_time_equal(expected, x_admin_token):
        raise HTTPException(status_code=401, detail="bad admin token")


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
AppName = Annotated[str, Path(pattern=APP_NAME_PATTERN)]
MANAGED_ROOT = FilePath("/srv/deployd")


def actor_of(request: Request) -> str:
    # The reverse proxy forwards the Basic Auth user; direct callers are just the token.
    user = request.headers.get("x-remote-user", "")
    return user if _ACTOR_RE.fullmatch(user) else "admin-token"


def _audit(request: Request, action: str, target: str | None, detail: str | None = None) -> None:
    request.app.state.store.audit(actor_of(request), action, target, detail)


class ReleaseSelection(BaseModel):
    release: str = Field(pattern=r"^(?:[0-9a-f]{40}-[0-9a-f]{32}|previous|b4deployd)$")


class WebsiteConfirmation(BaseModel):
    confirm: str


class AppRemoval(BaseModel):
    confirm: str
    website: Literal["restore", "keep"]


class FreezeRequest(BaseModel):
    frozen: bool


class AppCredentials(BaseModel):
    signing_secret: SecretStr | None = None
    generate_signing_secret: bool = False
    github_token: SecretStr | None = None
    remove_github_token: bool = False

    @field_validator("signing_secret", "github_token")
    @classmethod
    def validate_credential(cls, value):
        if value is not None:
            secret = value.get_secret_value()
            if (
                not 32 <= len(secret.encode()) <= 4096
                or secret != secret.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in secret)
            ):
                raise ValueError(
                    "credential must contain 32-4096 bytes without surrounding whitespace or control characters"
                )
        return value

    @model_validator(mode="after")
    def exclusive_actions(self):
        if self.signing_secret is not None and self.generate_signing_secret:
            raise ValueError("choose either a supplied or generated signing secret")
        if self.github_token is not None and self.remove_github_token:
            raise ValueError("choose either saving or removing a GitHub token")
        return self


class AppSetup(BaseModel):
    spec: AppSpec
    credentials: AppCredentials = Field(default_factory=AppCredentials)
    create_only: bool = False


def _release_app(request: Request, name: str, *, idle: bool = False) -> AppSpec:
    spec = get_app_registry().get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown app")
    if idle and request.app.state.store.has_active_deploys(name):
        raise HTTPException(status_code=409, detail="app has queued or running deployments")
    return spec


@router.get("/apps/{name}/releases")
async def list_app_releases(request: Request, name: AppName):
    spec = _release_app(request, name)
    try:
        result = runner.list_releases(spec)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store = request.app.state.store
    for release in result["releases"]:
        identity = release.get("release_id", release["name"])
        original = store.get_deploy(identity[41:]) if identity != "previous" else None
        release["can_activate"] = not release["active"] and (
            release["name"] in {"previous", "b4deployd"}
            or (
                original is not None
                and original["app"] == name
                and original["commit_sha"] == release["commit_sha"]
                and original["status"] == "succeeded"
            )
        )
    result["busy"] = store.has_active_deploys(name)
    return result


@router.post("/apps/{name}/releases/activate", status_code=202)
async def activate_app_release(request: Request, name: AppName, selection: ReleaseSelection):
    store = request.app.state.store
    with config_lock():
        spec = _release_app(request, name, idle=True)
        try:
            target = runner.local_release(spec, selection.release)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if target.resolve() == runner.current_release_path(spec):
            raise HTTPException(status_code=409, detail="release is already active")
        original = (
            store.get_deploy(selection.release[41:]) if selection.release != "previous" else None
        )
        if selection.release not in {"previous", "b4deployd"} and (
            original is None
            or original["app"] != name
            or original["commit_sha"] != selection.release[:40]
            or original["status"] != "succeeded"
        ):
            raise HTTPException(
                status_code=409, detail="release has no successful deployment record"
            )
        deploy_id = store.create_deploy(
            name,
            original["commit_sha"] if original else "0" * 40,
            original["artifact_url"] if original else f"local-release://{selection.release}",
            original["artifact_sha256"] if original else "0" * 64,
            f"activate:{selection.release}",
            kind="activate",
        )
        request.app.state.queue.enqueue_activation(name, deploy_id, selection.release)
        _audit(request, "release.activate", name, selection.release)
    return {"deploy_id": deploy_id, "status": "queued"}


@router.post("/apps/{name}/releases/cleanup", status_code=202)
async def cleanup_app_release(request: Request, name: AppName, selection: ReleaseSelection):
    store = request.app.state.store
    with config_lock():
        spec = _release_app(request, name, idle=True)
        try:
            runner.removable_release(spec, selection.release)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        deploy_id = store.create_deploy(
            name,
            "0" * 40,
            f"local-cleanup://{selection.release}",
            "0" * 64,
            f"cleanup:{selection.release}",
            kind="cleanup",
        )
        request.app.state.queue.enqueue_cleanup(name, deploy_id, selection.release)
        _audit(request, "release.cleanup", name, selection.release)
    return {"deploy_id": deploy_id, "status": "queued"}


@router.get("/apps/{name}/website")
async def inspect_website(request: Request, name: AppName):
    spec = _release_app(request, name)
    if request.app.state.store.has_active_deploys(name):
        return {"status": "busy"}
    try:
        return await website.inspect(name, spec)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/apps/{name}/website/connect", status_code=202)
async def connect_website(request: Request, name: AppName, confirmation: WebsiteConfirmation):
    if confirmation.confirm != name:
        raise HTTPException(
            status_code=422, detail="type the app name to confirm the live website switch"
        )
    with config_lock():
        spec = _release_app(request, name, idle=True)
        try:
            website.arguments(name, spec, "connect")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        deploy_id = request.app.state.store.create_deploy(
            name, "0" * 40, "local-website://connect", "0" * 64, f"connect:{name}", kind="connect"
        )
        request.app.state.queue.enqueue_connection(name, deploy_id)
        _audit(request, "website.connect", name)
    return {"deploy_id": deploy_id, "status": "queued"}


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
        name: {
            **spec.model_dump(mode="json"),
            "secret": _secret_info(name),
            "github": github_token_info(name),
        }
        for name, spec in get_app_registry().items()
    }


@router.get("/apps/{name}/status")
async def app_status(request: Request, name: AppName):
    spec = _release_app(request, name)
    store = request.app.state.store
    current = runner.current_release_path(spec)
    current_name = None
    if current is not None:
        if spec.release_layout == "directory":
            try:
                current_name = runner.directory_layout.read_manifest(current)["name"]
            except ValueError:
                current_name = "unreadable"
        else:
            current_name = current.name
    return {
        "app": name,
        "frozen": spec.frozen,
        "busy": store.has_active_deploys(name),
        "queued": store.count_queued(name),
        "current_release": current_name,
        "last_deploy": store.last_deploy(name),
        "last_health": store.last_step(name, "health"),
    }


@router.post("/apps/{name}/freeze")
async def freeze_app(request: Request, name: AppName, body: FreezeRequest):
    with config_lock():
        try:
            spec = set_frozen(name, body.frozen)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown app") from exc
        _audit(request, "app.freeze" if body.frozen else "app.unfreeze", name)
    return {"app": name, "frozen": spec.frozen}


def _validate_app_update(request: Request, name: str, spec: AppSpec):
    if request.app.state.store.has_active_deploys(name):
        raise HTTPException(status_code=409, detail="app has queued or running deployments")
    existing = get_app_registry().get(name)
    if existing and existing.site_path and spec.site_path != existing.site_path:
        raise HTTPException(status_code=409, detail="the local site path is fixed after setup")
    if (
        existing
        and (existing.release_layout, existing.releases_dir, existing.current_link)
        != (spec.release_layout, spec.releases_dir, spec.current_link)
        and (
            os.path.lexists(existing.current_link)
            or (existing.releases_dir.exists() and any(existing.releases_dir.iterdir()))
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="layout and paths cannot change while releases exist; use a new application or migrate offline",
        )
    if spec.release_layout == "directory":
        try:
            runner.directory_layout.prepare(spec)
            runner.directory_layout.reconcile(spec)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/setup")
async def setup_defaults():
    return {"github_server_token_configured": bool(get_settings().github_token)}


@router.post("/apps/{name}/github-actions")
async def github_actions_setup(request: Request, name: AppName, settings: GitHubActionsSettings):
    with config_lock():
        spec = _release_app(request, name)
        if not spec.github_repository or not spec.deploy_url:
            raise HTTPException(
                status_code=422, detail="set the GitHub repository and public deployd URL first"
            )
        if not spec.deploy_url.startswith("https://"):
            raise HTTPException(
                status_code=422, detail="GitHub Actions requires a public HTTPS deployd URL"
            )
        expected_prefix = f"https://api.github.com/repos/{spec.github_repository}/releases/assets/"
        if spec.artifact.allowed_url_prefix != expected_prefix:
            raise HTTPException(
                status_code=422,
                detail="update the app's GitHub artifact source before generating its workflow",
            )
        static_site = spec.restart.command == [
            "/usr/bin/test",
            "-s",
            f"{spec.current_link}/index.html",
        ]
        bundle = setup_bundle(name, spec.github_repository, settings, static_site=static_site)
        updated = spec.model_copy(update={"github_actions": settings})
        try:
            upsert_app(name, updated)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409, detail="could not save GitHub Actions settings"
            ) from exc
        _audit(request, "app.github_actions", name)
    return {**bundle, "settings": settings.model_dump()}


@router.post("/apps/{name}/setup")
async def setup_app(request: Request, name: AppName, setup: AppSetup):
    with config_lock():
        if setup.create_only and name in get_app_registry():
            raise HTTPException(
                status_code=409, detail="application already exists; edit it instead"
            )
        credentials = setup.credentials
        changes = {}
        generated = None
        if credentials.generate_signing_secret or credentials.signing_secret is not None:
            if _secret_key(name) in os.environ:
                raise HTTPException(
                    status_code=409, detail="signing secret is managed by the service environment"
                )
            generated = pysecrets.token_hex(32) if credentials.generate_signing_secret else None
            changes[_secret_key(name)] = generated or credentials.signing_secret.get_secret_value()
        if credentials.github_token is not None or credentials.remove_github_token:
            if github_secret_key(name) in os.environ:
                raise HTTPException(
                    status_code=409, detail="GitHub token is managed by the service environment"
                )
            if credentials.github_token is not None and not setup.spec.github_repository:
                raise HTTPException(
                    status_code=422, detail="configure a GitHub repository before storing its token"
                )
            changes[github_secret_key(name)] = (
                credentials.github_token.get_secret_value() if credentials.github_token else None
            )
        if not setup.create_only and name not in get_app_registry():
            raise HTTPException(status_code=404, detail="unknown app")
        existing = get_app_registry().get(name)
        expected = (
            (existing.release_layout, existing.releases_dir, existing.current_link)
            if existing
            else (
                "directory",
                MANAGED_ROOT / name / "releases",
                MANAGED_ROOT / name / "releases/current",
            )
        )
        if (
            setup.spec.release_layout,
            setup.spec.releases_dir,
            setup.spec.current_link,
        ) != expected:
            raise HTTPException(
                status_code=422, detail="release layout and paths are managed by deployd"
            )
        _validate_app_update(request, name, setup.spec)
        try:
            configure_app(name, setup.spec, changes)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="could not save application; check service permissions and runtime configuration",
            ) from exc
        _audit(
            request,
            "app.create" if existing is None else "app.update",
            name,
            "; ".join(
                part
                for part, on in (
                    ("signing secret set", _secret_key(name) in changes),
                    ("github token set", changes.get(github_secret_key(name)) is not None),
                    ("github token removed", credentials.remove_github_token),
                )
                if on
            )
            or None,
        )
    return {
        "status": "saved",
        "app": name,
        "secret": generated,
        "fingerprint": _secret_info(name)["fingerprint"],
        "config": setup.spec.model_dump(mode="json"),
    }


@router.put("/apps/{name}")
async def upsert_app_route(request: Request, name: AppName, spec: AppSpec):
    with config_lock():
        _validate_app_update(request, name, spec)
        upsert_app(name, spec)
        _audit(request, "app.update", name)
    return {"status": "saved", "app": name}


@router.delete("/apps/{name}")
async def delete_app(
    request: Request, response: Response, name: AppName, selection: AppRemoval | None = None
):
    with config_lock():
        if request.app.state.store.has_active_deploys(name):
            raise HTTPException(status_code=409, detail="app has queued or running deployments")
        if _secret_key(name) in os.environ:
            raise HTTPException(
                status_code=409,
                detail=f"unset {_secret_key(name)} from the service environment before deletion",
            )
        if github_secret_key(name) in os.environ:
            raise HTTPException(
                status_code=409,
                detail="unset the app GitHub token from the service environment before deletion",
            )
        spec = _release_app(request, name)
        if spec.site_path and (selection is None or selection.confirm != name):
            raise HTTPException(
                status_code=422,
                detail="confirm the app name and choose whether to restore the website path or keep its link",
            )
        if spec.site_path and selection.website == "restore":
            try:
                website.arguments(name, spec, "detach")
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            deploy_id = request.app.state.store.create_deploy(
                name, "0" * 40, "local-website://remove", "0" * 64, f"remove:{name}", kind="remove"
            )
            request.app.state.queue.enqueue_removal(name, deploy_id)
            _audit(request, "app.delete", name, "website restore queued")
            response.status_code = 202
            return {"status": "queued", "app": name, "deploy_id": deploy_id}
        if not delete_app_config(name):
            raise HTTPException(status_code=404, detail="unknown app")
        _audit(request, "app.delete", name)
    return {"status": "deleted", "app": name}


@router.post("/apps/{name}/rotate-secret")
async def rotate_secret(request: Request, name: AppName):
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
        _audit(request, "secret.rotate", name)
    return {
        # shown exactly once — copy it into the CI secret now
        "secret": new_secret,
        "fingerprint": hashlib.sha256(new_secret.encode()).hexdigest()[:12],
        "env_override": False,
        "warning": None,
    }


@router.get("/deploys")
async def list_deploys(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    app: Annotated[str | None, Query(pattern=APP_NAME_PATTERN)] = None,
    status: DeployStatus | None = None,
):
    return request.app.state.store.list_deploys(
        limit=limit, offset=offset, app=app, status=status.value if status else None
    )


@router.post("/deploys/{deploy_id}/cancel")
async def cancel_deploy(request: Request, deploy_id: str):
    store = request.app.state.store
    if store.get_deploy(deploy_id) is None:
        raise HTTPException(status_code=404, detail="unknown deploy")
    if not store.cancel_deploy(deploy_id, actor_of(request)):
        raise HTTPException(status_code=409, detail="only queued deploys can be cancelled")
    _audit(request, "deploy.cancel", deploy_id)
    return {"deploy_id": deploy_id, "status": "cancelled"}


@router.post("/deploys/{deploy_id}/redeploy")
async def redeploy(request: Request, deploy_id: str):
    store = request.app.state.store
    old = store.get_deploy(deploy_id)
    if old is None:
        raise HTTPException(status_code=404, detail="unknown deploy")
    if old["kind"] != "artifact":
        raise HTTPException(status_code=409, detail="only artifact deploys can be redeployed")
    with config_lock():
        spec = get_app_registry().get(old["app"])
        if spec is None:
            raise HTTPException(status_code=409, detail="app no longer registered")
        if request.app.state.queue.is_removing(old["app"]):
            raise HTTPException(status_code=409, detail="app removal is in progress")
        if not spec.artifact.allows_initial_url(old["artifact_url"]):
            raise HTTPException(status_code=409, detail="artifact URL is no longer allowed")
        new_id = store.create_deploy(
            old["app"],
            old["commit_sha"],
            old["artifact_url"],
            old["artifact_sha256"],
            f"redeploy:{deploy_id[:8]}",
        )
        store.supersede_queued(old["app"], new_id)
        request.app.state.queue.enqueue(old["app"], new_id)
        _audit(request, "deploy.redeploy", old["app"], f"{deploy_id} -> {new_id}")
    return {"deploy_id": new_id, "status": "queued"}


@router.get("/audit")
async def list_audit(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return request.app.state.store.list_audit(limit=limit, offset=offset)
