import os
import posixpath
import re
import tempfile
import threading
from contextlib import contextmanager, suppress
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_APP_NAME_RE = re.compile(APP_NAME_PATTERN)
_CONFIG_LOCK = threading.RLock()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYD_", env_file=".env", extra="ignore")

    db_path: Path = Path("deployd.sqlite3")
    apps_config: Path = Path("config/apps.yaml")
    secrets_file: Path = Path("config/secrets.env")
    admin_token: str | None = None
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8300, ge=1, le=65_535)
    max_request_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    # HMAC replay protection
    timestamp_window_seconds: int = Field(default=300, ge=30, le=3600)

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


class MigrateSpec(BaseModel):
    command: list[str] | None = None

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (not value or any(not part for part in value)):
            raise ValueError("migration command must be null or a non-empty argument list")
        return value


class RestartSpec(BaseModel):
    command: list[str] = Field(min_length=1)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not part for part in value):
            raise ValueError("restart command arguments cannot be empty")
        return value


class HealthSpec(BaseModel):
    url: str
    retries: int = Field(default=10, ge=1, le=100)
    interval_seconds: float = Field(default=3, ge=0, le=300)

    @field_validator("url")
    @classmethod
    def validate_health_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("health URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("health URL cannot contain credentials")
        return value


class AppSpec(BaseModel):
    releases_dir: Path
    current_link: Path
    keep_releases: int = Field(default=5, ge=1, le=100)
    artifact: ArtifactRules
    migrate: MigrateSpec = Field(default_factory=MigrateSpec)
    restart: RestartSpec
    health: HealthSpec

    @field_validator("releases_dir", "current_link")
    @classmethod
    def validate_managed_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("managed paths must be absolute")
        if value.parent == value:
            raise ValueError("filesystem root cannot be a managed path")
        return value

    @model_validator(mode="after")
    def validate_path_relationships(self):
        # Normalize lexical paths without following an existing ``current`` symlink.
        releases = Path(os.path.abspath(self.releases_dir))
        current = Path(os.path.abspath(self.current_link))
        if current == releases or current.is_relative_to(releases):
            raise ValueError("current_link must be outside releases_dir")
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
    key = _secret_key(app_name)
    if key in os.environ:
        return os.environ[key]
    path = get_settings().secrets_file
    with _CONFIG_LOCK:
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
    key = _secret_key(app_name)
    path = get_settings().secrets_file
    with _CONFIG_LOCK:
        lines = []
        if path.exists():
            lines = [ln for ln in path.read_text().splitlines() if not ln.startswith(f"{key}=")]
        lines.append(f"{key}={secret}")
        _atomic_write_text(path, "\n".join(lines) + "\n", mode=0o600)


def remove_app_secret(app_name: str) -> None:
    validate_app_name(app_name)
    key = _secret_key(app_name)
    path = get_settings().secrets_file
    with _CONFIG_LOCK:
        if not path.exists():
            return
        lines = [ln for ln in path.read_text().splitlines() if not ln.startswith(f"{key}=")]
        _atomic_write_text(path, "\n".join(lines) + "\n" if lines else "", mode=0o600)


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
