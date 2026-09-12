import os
import posixpath
import re
import tempfile
import threading
from contextlib import contextmanager, suppress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .github_actions import GitHubActionsSettings

APP_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_APP_NAME_RE = re.compile(APP_NAME_PATTERN)
_CONFIG_LOCK = threading.RLock()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYD_", env_file=".env", extra="ignore")

    db_path: Path = Path("deployd.sqlite3")
    apps_config: Path = Path("config/apps.yaml")
    secrets_file: Path = Path("config/secrets.env")
    admin_token: str | None = None
    github_token: SecretStr | None = None
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8300, ge=1, le=65_535)
    max_request_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    # HMAC replay protection
    timestamp_window_seconds: int = Field(default=300, ge=30, le=3600)
    history_keep_days: int = Field(default=90, ge=1, le=3650)
    # Longest wait for an in-flight cutover to finish on shutdown; systemd's stop
    # timeout must exceed it.
    drain_timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @field_validator("admin_token", mode="before")
    @classmethod
    def validate_admin_token(cls, value):
        if value in (None, ""):
            return None
        if not isinstance(value, str) or len(value.encode()) < 32:
            raise ValueError("admin token must contain at least 32 bytes")
        return value


class ArtifactRules(BaseModel):
    allowed_url_prefix: str
    allowed_redirect_hosts: list[str] = Field(default_factory=list)
    allow_private_networks: bool = False
    max_download_bytes: int = Field(default=1_073_741_824, ge=1, le=10_737_418_240)
    max_extract_bytes: int = Field(default=2_147_483_648, ge=1, le=21_474_836_480)
    max_extract_files: int = Field(default=10_000, ge=1, le=100_000)

    @field_validator("allowed_url_prefix")
    @classmethod
    def validate_url_prefix(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("allowed_url_prefix must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("allowed_url_prefix cannot contain credentials, query, or fragment")
        if not parsed.path.endswith("/"):
            raise ValueError("allowed_url_prefix path must end with /")
        return value

    @field_validator("allowed_redirect_hosts")
    @classmethod
    def validate_redirect_hosts(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            host = value.strip().lower().rstrip(".")
            if not host or "/" in host or ":" in host or "@" in host:
                raise ValueError("redirect hosts must be hostnames without scheme, path, or port")
            normalized.append(host)
        return sorted(set(normalized))

    def allows_initial_url(self, value: str) -> bool:
        candidate = urlsplit(value)
        prefix = urlsplit(self.allowed_url_prefix)
        candidate_path = posixpath.normpath(unquote(candidate.path))
        prefix_path = posixpath.normpath(unquote(prefix.path)).rstrip("/") + "/"
        return (
            candidate.scheme == prefix.scheme
            and candidate.hostname == prefix.hostname
            and _effective_port(candidate) == _effective_port(prefix)
            and candidate.username is None
            and candidate.password is None
            and candidate_path.startswith(prefix_path)
        )

    def allows_redirect_url(self, value: str) -> bool:
        candidate = urlsplit(value)
        prefix = urlsplit(self.allowed_url_prefix)
        if candidate.username is not None or candidate.password is not None:
            return False
        if candidate.scheme != prefix.scheme:
            return False
        if candidate.hostname == prefix.hostname and _effective_port(candidate) == _effective_port(
            prefix
        ):
            return self.allows_initial_url(value)
        return candidate.hostname in self.allowed_redirect_hosts and _effective_port(
            candidate
        ) == _effective_port(prefix)


def _validate_optional_command(value: list[str] | None, label: str) -> list[str] | None:
    if value is not None and (not value or any(not part for part in value)):
        raise ValueError(f"{label} must be null or a non-empty argument list")
    return value


class MigrateSpec(BaseModel):
    command: list[str] | None = None
    timeout_seconds: int = Field(default=600, ge=1, le=3600)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str] | None) -> list[str] | None:
        return _validate_optional_command(value, "migration command")


class RestartSpec(BaseModel):
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not part for part in value):
            raise ValueError("restart command arguments cannot be empty")
        return value


_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
# Service secrets never travel to commands, whatever the app asks for.
_ENV_PASSTHROUGH_DENY = ("DEPLOYD_ADMIN_TOKEN", "DEPLOYD_GITHUB_TOKEN", "DEPLOYD_SECRET_")


def validate_env_passthrough(value: list[str]) -> list[str]:
    seen = []
    for name in value:
        if not _ENV_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name}")
        if name.startswith(_ENV_PASSTHROUGH_DENY):
            raise ValueError(f"{name} cannot be passed to commands")
        if name not in seen:
            seen.append(name)
    return seen


class HooksSpec(BaseModel):
    before_cutover: list[str] | None = None
    after_health: list[str] | None = None
    timeout_seconds: int = Field(default=600, ge=1, le=3600)

    @field_validator("before_cutover", "after_health")
    @classmethod
    def validate_command(cls, value: list[str] | None) -> list[str] | None:
        return _validate_optional_command(value, "hook command")


class HealthSpec(BaseModel):
    url: str | None = None
    retries: int = Field(default=10, ge=1, le=100)
    interval_seconds: float = Field(default=3, ge=0, le=300)
    # {commit_sha} in either expectation is replaced with the deployed SHA, so a
    # stale process answering 200 cannot pass as the new release.
    expect_body: str | None = Field(default=None, max_length=500)
    expect_header: str | None = Field(default=None, max_length=500)

    @field_validator("url")
    @classmethod
    def validate_health_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("health URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("health URL cannot contain credentials")
        return value

    @field_validator("expect_body", "expect_header")
    @classmethod
    def validate_expectation(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("expect_header")
    @classmethod
    def validate_header_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name, sep, expected = value.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z0-9-]+", name.strip()) or not expected.strip():
            raise ValueError("expected header must look like Header-Name: value")
        return f"{name.strip()}: {expected.strip()}"


NOTIFY_EVENTS = ("succeeded", "failed", "rolled_back")


class NotifySpec(BaseModel):
    url: str | None = None
    events: list[Literal["succeeded", "failed", "rolled_back"]] = Field(
        default_factory=lambda: ["failed", "rolled_back"]
    )
    format: Literal["generic", "slack", "discord"] = "generic"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("notification URL must be an absolute HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("notification URL cannot contain credentials")
        return value

    @field_validator("events")
    @classmethod
    def dedupe_events(cls, value: list[str]) -> list[str]:
        return [event for event in NOTIFY_EVENTS if event in value]


class AppSpec(BaseModel):
    github_actions: GitHubActionsSettings | None = None
    github_repository: str | None = None
    deploy_url: str | None = None
    site_path: Path | None = None
    releases_dir: Path
    current_link: Path
    release_layout: Literal["symlink", "directory"] = "symlink"
    keep_previous: int = Field(default=1, ge=0, le=99)
    auto_cleanup: bool = True
    # A frozen app rejects CI deploys with 423; local activation stays available.
    frozen: bool = False
    artifact: ArtifactRules
    migrate: MigrateSpec = Field(default_factory=MigrateSpec)
    hooks: HooksSpec = Field(default_factory=HooksSpec)
    restart: RestartSpec
    health: HealthSpec = Field(default_factory=HealthSpec)
    notify: NotifySpec = Field(default_factory=NotifySpec)
    # Names of service environment variables forwarded to migrate, restart and hooks
    # (for example a DSN variable); everything else stays with the service.
    env_passthrough: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("env_passthrough")
    @classmethod
    def validate_passthrough(cls, value: list[str]) -> list[str]:
        return validate_env_passthrough(value)

    @field_validator("github_repository")
    @classmethod
    def validate_repository(cls, value):
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value
        ):
            raise ValueError("GitHub repository must be OWNER/REPO")
        return value

    @field_validator("deploy_url")
    @classmethod
    def validate_deploy_url(cls, value):
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "deploy URL must be an HTTP(S) URL without credentials, query, or fragment"
            )
        return value.rstrip("/")

    @model_validator(mode="before")
    @classmethod
    def migrate_retention(cls, value):
        if isinstance(value, dict) and "keep_releases" in value:
            value = dict(value)
            legacy = value.pop("keep_releases")
            if not isinstance(legacy, int) or isinstance(legacy, bool) or not 1 <= legacy <= 100:
                raise ValueError("keep_releases must be between 1 and 100")
            value.setdefault("keep_previous", legacy - 1)
        return value

    @field_validator("keep_previous", mode="before")
    @classmethod
    def default_unset_retention(cls, value):
        # Earlier configurations used null for a choice that had not yet been made.
        return 1 if value is None else value

    @field_validator("releases_dir", "current_link")
    @classmethod
    def validate_managed_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("managed paths must be absolute")
        if value.parent == value:
            raise ValueError("filesystem root cannot be a managed path")
        return value

    @field_validator("site_path")
    @classmethod
    def validate_site_path(cls, value):
        if value is not None:
            value = Path(os.path.abspath(value)) if value.is_absolute() else value
            if not value.is_absolute() or len(value.parts) < 4:
                raise ValueError(
                    "site path must be an absolute app-specific path, such as /var/www/example.com"
                )
        return value

    @model_validator(mode="after")
    def validate_path_relationships(self):
        # Normalize lexical paths without following an existing ``current`` symlink.
        releases = Path(os.path.abspath(self.releases_dir))
        current = Path(os.path.abspath(self.current_link))
        if self.site_path and (
            self.site_path == releases
            or self.site_path.is_relative_to(releases)
            or releases.is_relative_to(self.site_path)
        ):
            raise ValueError("site path must be separate from the managed release directory")
        if self.release_layout == "directory":
            if current != releases / "current":
                raise ValueError("directory layout requires current_link = releases_dir/current")
        elif current == releases or current.is_relative_to(releases):
            raise ValueError("current_link must be outside releases_dir")
        if (
            self.github_repository
            and self.artifact.allowed_url_prefix
            != f"https://api.github.com/repos/{self.github_repository}/releases/assets/"
        ):
            raise ValueError("artifact prefix must match the configured GitHub repository")
        return self


def _effective_port(parsed) -> int | None:
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None


def validate_app_name(name: str) -> str:
    if not _APP_NAME_RE.fullmatch(name):
        raise ValueError("app name must be 1-64 lowercase letters, digits, or interior dashes")
    return name


@contextmanager
def config_lock():
    with _CONFIG_LOCK:
        yield


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_app_registry() -> dict[str, AppSpec]:
    raw = yaml.safe_load(get_settings().apps_config.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("apps"), dict):
        raise ValueError("apps config must contain an 'apps' mapping")
    registry = {}
    for name, spec in raw["apps"].items():
        validate_app_name(name)
        registry[name] = AppSpec.model_validate(spec)
    return registry


def _secret_key(app_name: str) -> str:
    return "DEPLOYD_SECRET_" + app_name.upper().replace("-", "_")


def get_app_secret(app_name: str) -> str | None:
    """Env var wins over the secrets file, so ops can pin a secret."""
    return _get_secret_value(_secret_key(app_name))


def github_secret_key(app_name: str) -> str:
    validate_app_name(app_name)
    return "DEPLOYD_GITHUB_TOKEN_" + app_name.upper().replace("-", "_")


def get_app_github_token(app_name: str | None) -> str | None:
    token = _get_secret_value(github_secret_key(app_name)) if app_name else None
    fallback = get_settings().github_token
    return token if token is not None else fallback.get_secret_value() if fallback else None


def github_token_info(app_name: str) -> dict:
    key = github_secret_key(app_name)
    own = _get_secret_value(key)
    token = get_app_github_token(app_name)
    source = "environment" if key in os.environ else "app" if own else "server" if token else None
    return {"configured": bool(token), "source": source, "env_override": key in os.environ}


def _get_secret_value(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    path = get_settings().secrets_file
    with _CONFIG_LOCK:
        if path.is_symlink():
            raise ValueError("secrets file must not be a symbolic link")
        if path.exists():
            for line in path.read_text().splitlines():
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == key and not k.lstrip().startswith("#"):
                    return v.strip()
    return None


def set_app_secret(app_name: str, secret: str) -> None:
    validate_app_name(app_name)
    if len(secret.encode()) < 32 or "\n" in secret or "\r" in secret:
        raise ValueError("secret must be a single-line value containing at least 32 bytes")
    _update_secret_values({_secret_key(app_name): secret})


def _update_secret_values(changes: dict[str, str | None]) -> None:
    path = get_settings().secrets_file
    with _CONFIG_LOCK:
        if path.is_symlink():
            raise ValueError("secrets file must not be a symbolic link")
        lines = []
        if path.exists():
            lines = [
                ln
                for ln in path.read_text().splitlines()
                if ln.partition("=")[0].strip() not in changes
            ]
        lines.extend(f"{key}={value}" for key, value in changes.items() if value is not None)
        _atomic_write_text(path, "\n".join(lines) + "\n", mode=0o600)


def remove_app_secret(app_name: str) -> None:
    validate_app_name(app_name)
    if get_settings().secrets_file.exists():
        _update_secret_values({_secret_key(app_name): None, github_secret_key(app_name): None})


def configure_app(name: str, spec: AppSpec, changes: dict[str, str | None]) -> None:
    with _CONFIG_LOCK:
        path = get_settings().secrets_file
        if path.is_symlink():
            raise ValueError("secrets file must not be a symbolic link")
        original = path.read_text() if path.exists() else ""
        if changes:
            _update_secret_values(changes)
        try:
            upsert_app(name, spec)
        except Exception:
            if changes:
                _atomic_write_text(path, original, mode=0o600)
            raise


def save_app_registry(registry: dict[str, AppSpec]) -> None:
    with _CONFIG_LOCK:
        for name in registry:
            validate_app_name(name)
        data = {"apps": {name: spec.model_dump(mode="json") for name, spec in registry.items()}}
        _atomic_write_text(
            get_settings().apps_config,
            yaml.safe_dump(data, sort_keys=False),
            mode=0o600,
        )
        get_app_registry.cache_clear()


def upsert_app(name: str, spec: AppSpec) -> None:
    validate_app_name(name)
    with _CONFIG_LOCK:
        registry = dict(get_app_registry())
        registry[name] = spec
        save_app_registry(registry)


def set_frozen(name: str, frozen: bool) -> AppSpec:
    with _CONFIG_LOCK:
        registry = get_app_registry()
        if name not in registry:
            raise KeyError(name)
        updated = registry[name].model_copy(update={"frozen": frozen})
        upsert_app(name, updated)
        return updated


def delete_app_config(name: str) -> bool:
    validate_app_name(name)
    with _CONFIG_LOCK:
        registry = dict(get_app_registry())
        if name not in registry:
            return False
        del registry[name]
        save_app_registry(registry)
        remove_app_secret(name)
        return True


def _atomic_write_text(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, mode)
    except Exception:
        with suppress(OSError):
            os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
