"""Management surface. The deploy HMAC does not cover it; it has its own auth.

Three routers, so a new endpoint is owner-only unless it is placed elsewhere on purpose:
- ``router``: the owner (admin token) only. Everything that changes configuration.
- ``app_router``: one application; the owner, or a member it is assigned to.
- ``shared_router``: any signed-in caller; handlers filter by the caller's applications.
"""

import hashlib
import os
import re
import secrets as pysecrets
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from .. import account, auth, envfile
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
from ..github_actions import GitHubActionsSettings, render_workflow, setup_bundle
from ..models import DeployStatus
from ..worker import runner, website

_ACTOR_RE = re.compile(r"^[A-Za-z0-9@._-]{1,64}$")


AppName = Annotated[str, Path(pattern=APP_NAME_PATTERN)]
MANAGED_ROOT = FilePath("/srv/deployd")


def _app_access(request: Request, name: AppName) -> auth.Principal:
    return auth.require_app_access(request, name)


router = APIRouter(prefix="/admin", dependencies=[Depends(auth.require_admin)])
app_router = APIRouter(prefix="/admin/apps/{name}", dependencies=[Depends(_app_access)])
shared_router = APIRouter(prefix="/admin", dependencies=[Depends(auth.require_principal)])
auth_router = APIRouter(prefix="/auth")


def actor_of(request: Request) -> str:
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None and not principal.admin:
        return principal.name
    # The reverse proxy forwards the Basic Auth user; direct callers are just the token.
    user = request.headers.get("x-remote-user", "")
    return user if _ACTOR_RE.fullmatch(user) else "admin-token"


def _audit(
    request: Request,
    action: str,
    target: str | None,
    detail: str | None = None,
    *,
    app: str | None = None,
) -> None:
    """``app`` ties the entry to an application so its members can see it."""
    if app is None and target in get_app_registry():
        app = target
    request.app.state.store.audit(actor_of(request), action, target, detail, app)


class ReleaseSelection(BaseModel):
    release: str = Field(pattern=r"^(?:[0-9a-f]{40}-[0-9a-f]{32}|previous|b4deployd)$")


class PasswordChange(BaseModel):
    current_password: SecretStr
    new_password: SecretStr

    @model_validator(mode="after")
    def validate_passwords(self):
        current = self.current_password.get_secret_value()
        new = self.new_password.get_secret_value()
        if not current or len(current.encode()) > 4096:
            raise ValueError("invalid current password length")
        if len(new) < 12 or len(new.encode()) > 72:
            raise ValueError(
                "new password must be at least 12 characters and at most 72 UTF-8 bytes"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in current + new):
            raise ValueError("passwords cannot contain control characters")
        if current == new:
            raise ValueError("new password must be different")
        return self


def management_user(request: Request) -> str:
    username = request.headers.get("x-remote-user", "")
    if not _ACTOR_RE.fullmatch(username):
        raise HTTPException(403, "Sign in through the management web interface first.")
    return username


@router.get("/account")
def get_account(request: Request):
    return {
        "username": management_user(request),
        "password_change_available": account.HELPER.is_file(),
    }


@router.post("/account/password")
def change_management_password(body: PasswordChange, request: Request):
    username = management_user(request)
    try:
        account.change_password(
            username, body.current_password.get_secret_value(), body.new_password.get_secret_value()
        )
    except account.PasswordError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    _audit(request, "account.password.change", username)
    return {"changed": True}


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr


class OwnPasswordChange(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


def _new_password(value: SecretStr | None, generate: bool) -> tuple[str | None, str | None]:
    """Returns (password, generated): generated is set only when deployd made it up."""
    if generate:
        generated = auth.generate_password()
        return generated, generated
    if value is None:
        return None, None
    password = value.get_secret_value()
    problem = auth.password_problem(password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return password, None


class MemberCreate(BaseModel):
    username: str = Field(pattern=auth.USERNAME_PATTERN)
    password: SecretStr | None = None
    generate_password: bool = False
    apps: list[Annotated[str, Field(pattern=APP_NAME_PATTERN)]] = Field(
        default_factory=list, max_length=500
    )

    @model_validator(mode="after")
    def one_password_source(self):
        if (self.password is None) == (not self.generate_password):
            raise ValueError("give a password or ask for a generated one")
        return self


class MemberUpdate(BaseModel):
    password: SecretStr | None = None
    generate_password: bool = False
    disabled: bool | None = None
    apps: list[Annotated[str, Field(pattern=APP_NAME_PATTERN)]] | None = Field(
        default=None, max_length=500
    )

    @model_validator(mode="after")
    def one_password_source(self):
        if self.password is not None and self.generate_password:
            raise ValueError("give a password or ask for a generated one, not both")
        return self


MemberName = Annotated[str, Path(pattern=auth.USERNAME_PATTERN)]


def _known_apps(apps: list[str]) -> list[str]:
    unknown = sorted(set(apps) - set(get_app_registry()))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown application: {', '.join(unknown)}")
    return sorted(set(apps))


@auth_router.post("/login")
async def login(request: Request, body: Credentials):
    username = body.username.strip().lower()
    session = auth.login(request.app.state.store, username, body.password.get_secret_value())
    request.app.state.store.audit(username, "member.login", username, None)
    return {**session, "username": username}


@shared_router.post("/logout")
async def logout(request: Request):
    principal = request.state.principal
    if principal.session:
        request.app.state.store.delete_session(principal.session)
    return {"signed_out": True}


@shared_router.get("/me")
async def who_am_i(request: Request):
    principal = request.state.principal
    return {
        "role": "admin" if principal.admin else "member",
        "username": None if principal.admin else principal.name,
        "apps": None if principal.admin else sorted(principal.apps),
    }


@shared_router.post("/me/password")
async def change_own_password(request: Request, body: OwnPasswordChange):
    principal = request.state.principal
    if principal.admin:
        raise HTTPException(status_code=409, detail="the administrator uses the management login")
    store = request.app.state.store
    user = store.get_user(principal.name)
    current = body.current_password.get_secret_value()
    if user is None or not auth.verify_password(current, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    password, _ = _new_password(body.new_password, False)
    if password == current:
        raise HTTPException(status_code=422, detail="new password must be different")
    # Every other session of this member ends; the one making the change stays.
    store.update_user(
        principal.name, password_hash=auth.hash_password(password), keep_session=principal.session
    )
    _audit(request, "member.password.change", principal.name)
    return {"changed": True}


@router.get("/users")
async def list_members(request: Request):
    return request.app.state.store.list_users()


@router.post("/users", status_code=201)
async def create_member(request: Request, body: MemberCreate):
    if body.username in auth.RESERVED_USERNAMES:
        raise HTTPException(status_code=422, detail="that username is reserved")
    apps = _known_apps(body.apps)
    password, generated = _new_password(body.password, body.generate_password)
    if not request.app.state.store.create_user(body.username, auth.hash_password(password), apps):
        raise HTTPException(status_code=409, detail="a member with that username already exists")
    _audit(request, "member.create", body.username, f"apps: {', '.join(apps) or 'none'}")
    return {"username": body.username, "apps": apps, "password": generated}


@router.patch("/users/{username}")
async def update_member(request: Request, username: MemberName, body: MemberUpdate):
    apps = _known_apps(body.apps) if body.apps is not None else None
    password, generated = _new_password(body.password, body.generate_password)
    changed = request.app.state.store.update_user(
        username,
        password_hash=auth.hash_password(password) if password else None,
        disabled=body.disabled,
        apps=apps,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="unknown member")
    detail = "; ".join(
        part
        for part in (
            f"apps: {', '.join(apps) or 'none'}" if apps is not None else None,
            "password reset" if password else None,
            None if body.disabled is None else "disabled" if body.disabled else "enabled",
        )
        if part
    )
    _audit(request, "member.update", username, detail or None)
    return {"username": username, "password": generated}


@router.delete("/users/{username}")
async def delete_member(request: Request, username: MemberName):
    if not request.app.state.store.delete_user(username):
        raise HTTPException(status_code=404, detail="unknown member")
    _audit(request, "member.delete", username)
    return {"deleted": username}


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


@app_router.get("/releases")
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


@app_router.post("/releases/activate", status_code=202)
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


@app_router.post("/releases/cleanup", status_code=202)
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


@app_router.get("/website")
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


@router.post("/apps/{name}/website/repair", status_code=202)
async def repair_website(request: Request, name: AppName, confirmation: WebsiteConfirmation):
    if confirmation.confirm != name:
        raise HTTPException(status_code=422, detail="type the app name to confirm the repair")
    with config_lock():
        spec = _release_app(request, name, idle=True)
        try:
            website.arguments(name, spec, "repair")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        deploy_id = request.app.state.store.create_deploy(
            name, "0" * 40, "local-website://repair", "0" * 64, f"repair:{name}", kind="connect"
        )
        request.app.state.queue.enqueue_connection(name, deploy_id, "repair")
        _audit(request, "website.repair", name)
    return {"deploy_id": deploy_id, "status": "queued"}


class EnvFilePreview(BaseModel):
    content: str = Field(max_length=262_144)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EnvFileWrite(EnvFilePreview):
    # Every key the save drops must be named here, so a variable never disappears unnoticed.
    confirm_removed: list[str] = Field(default_factory=list, max_length=2000)
    restart: bool = False


class EnvFileRestore(BaseModel):
    confirm: str
    restart: bool = False


def _env_app(request: Request, name: str) -> AppSpec:
    spec = _release_app(request, name)
    if spec.env_file is None:
        raise HTTPException(status_code=409, detail="no .env file is configured for this app")
    return spec


async def _env_call(operation: str, name: str, spec: AppSpec, payload: dict | None = None) -> dict:
    try:
        return await envfile.call(operation, name, spec, payload)
    except envfile.EnvFileError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def _env_change_detail(result: dict, restart: bool) -> str:
    # Key names only: values never reach the audit log. Removals come first so the
    # length limit never hides them.
    parts = [f"variables: {result.get('keys_before', '?')} to {result.get('keys_after', '?')}"]
    parts += [
        f"{label}: {', '.join(result[label])}"
        for label in ("removed", "added", "changed")
        if result.get(label)
    ]
    if len(parts) == 1:
        parts.append("no variable changed (comments, order or spacing only)")
    parts.append("restart queued" if restart else "no restart")
    return "; ".join(parts)[:1000]


def _after_env_change(request: Request, name: str, result: dict, restart: bool) -> dict:
    deploy_id = None
    if restart and result.get("saved"):
        with config_lock():
            deploy_id = request.app.state.store.create_deploy(
                name, "0" * 40, "local-restart://env", "0" * 64, f"restart:{name}", kind="restart"
            )
            # The job puts this exact version back when the app does not come up healthy.
            revert = {"version": result.get("kept"), "expected_sha256": result.get("sha256")}
            request.app.state.queue.enqueue_restart(
                name, deploy_id, revert if revert["version"] else None
            )
    return {**result, "deploy_id": deploy_id}


@app_router.get("/env")
async def inspect_env_file(request: Request, name: AppName):
    spec = _env_app(request, name)
    if not envfile.HELPER.is_file():
        return {"path": str(spec.env_file), "available": False}
    return {
        "path": str(spec.env_file),
        "available": True,
        **(await _env_call("check", name, spec)),
    }


@app_router.get("/env/content")
async def read_env_file(request: Request, name: AppName):
    spec = _env_app(request, name)
    result = await _env_call("read", name, spec)
    _audit(request, "env.read", name, str(spec.env_file))
    return {"path": str(spec.env_file), **result}


@app_router.post("/env/preview")
async def preview_env_file(request: Request, name: AppName, body: EnvFilePreview):
    """What a save would change, by key name. Nothing is written."""
    spec = _env_app(request, name)
    try:
        return await _env_call(
            "preview",
            name,
            spec,
            {
                "content": body.content,
                "expected_sha256": body.expected_sha256,
                "confirm_removed": [],
            },
        )
    except HTTPException as exc:
        # A helper installed before previews existed rejects the operation by name.
        if "unknown operation" in str(exc.detail):
            raise HTTPException(
                status_code=503,
                detail="The .env helper on this server is older than deployd. "
                "Run update-ubuntu.sh there, then save again.",
            ) from None
        raise


@app_router.put("/env")
async def write_env_file(request: Request, name: AppName, body: EnvFileWrite):
    spec = _env_app(request, name)
    if request.app.state.queue.is_removing(name):
        raise HTTPException(status_code=409, detail="app removal is in progress")
    result = await _env_call(
        "write",
        name,
        spec,
        {
            "content": body.content,
            "expected_sha256": body.expected_sha256,
            "confirm_removed": body.confirm_removed,
        },
    )
    if result.get("saved"):
        _audit(request, "env.write", name, _env_change_detail(result, body.restart))
    return _after_env_change(request, name, result, body.restart)


@app_router.post("/env/restore")
async def restore_env_file(request: Request, name: AppName, body: EnvFileRestore):
    if body.confirm != name:
        raise HTTPException(status_code=422, detail="type the app name to confirm the restore")
    spec = _env_app(request, name)
    if request.app.state.queue.is_removing(name):
        raise HTTPException(status_code=409, detail="app removal is in progress")
    result = await _env_call("restore", name, spec)
    if result.get("saved"):
        _audit(request, "env.restore", name, _env_change_detail(result, body.restart))
    return _after_env_change(request, name, result, body.restart)


def _secret_info(app_name: str) -> dict:
    secret = get_app_secret(app_name)
    return {
        "configured": secret is not None,
        "fingerprint": hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else None,
        "env_override": _secret_key(app_name) in os.environ,
    }


@shared_router.get("/apps")
async def list_apps(request: Request):
    principal = request.state.principal
    return {
        name: {
            **spec.model_dump(mode="json"),
            "secret": _secret_info(name),
            "github": github_token_info(name),
        }
        for name, spec in get_app_registry().items()
        if principal.can(name)
    }


@app_router.get("/status")
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
        "last_deploy": store.last_deploy(name, kind="artifact"),
        "last_health": store.last_step(name, "health"),
        "website": website.link_state(spec),
    }


@app_router.post("/freeze")
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


def _github_actions_target(request: Request, name: str) -> tuple[AppSpec, bool]:
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
    return spec, static_site


@app_router.post("/github-actions/preview")
async def github_actions_preview(request: Request, name: AppName, settings: GitHubActionsSettings):
    """Render the workflow for the given settings without saving them."""
    spec, static_site = _github_actions_target(request, name)
    return {
        "workflow": render_workflow(name, settings, static_site=static_site),
        "settings": settings.model_dump(),
    }


@app_router.post("/github-actions")
async def github_actions_setup(request: Request, name: AppName, settings: GitHubActionsSettings):
    with config_lock():
        spec, static_site = _github_actions_target(request, name)
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
            _audit(request, "app.delete", name, "website restore queued", app=name)
            response.status_code = 202
            return {"status": "queued", "app": name, "deploy_id": deploy_id}
        if not delete_app_config(name):
            raise HTTPException(status_code=404, detail="unknown app")
        request.app.state.store.forget_app(name)
        _audit(request, "app.delete", name, app=name)
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


@shared_router.get("/deploys")
async def list_deploys(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    app: Annotated[str | None, Query(pattern=APP_NAME_PATTERN)] = None,
    status: DeployStatus | None = None,
):
    return request.app.state.store.list_deploys(
        limit=limit,
        offset=offset,
        app=app,
        status=status.value if status else None,
        apps=request.state.principal.scope,
    )


def _visible_deploy(request: Request, deploy_id: str) -> dict:
    """A deploy the caller may act on; one outside their applications does not exist for them."""
    row = request.app.state.store.get_deploy(deploy_id)
    if row is None or not request.state.principal.can(row["app"]):
        raise HTTPException(status_code=404, detail="unknown deploy")
    return row


@shared_router.post("/deploys/{deploy_id}/cancel")
async def cancel_deploy(request: Request, deploy_id: str):
    store = request.app.state.store
    row = _visible_deploy(request, deploy_id)
    if not store.cancel_deploy(deploy_id, actor_of(request)):
        raise HTTPException(status_code=409, detail="only queued deploys can be cancelled")
    _audit(request, "deploy.cancel", deploy_id, app=row["app"])
    return {"deploy_id": deploy_id, "status": "cancelled"}


@shared_router.post("/deploys/{deploy_id}/redeploy")
async def redeploy(request: Request, deploy_id: str):
    store = request.app.state.store
    old = _visible_deploy(request, deploy_id)
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


@shared_router.get("/audit")
async def list_audit(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return request.app.state.store.list_audit(
        limit=limit, offset=offset, apps=request.state.principal.scope
    )
