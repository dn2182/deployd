"""Settings (env) + app registry (yaml). Secrets only ever come from env."""

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYD_", env_file=".env", extra="ignore")

    db_path: Path = Path("deployd.sqlite3")
    apps_config: Path = Path("config/apps.yaml")
    secrets_file: Path = Path("config/secrets.env")
    admin_token: str | None = None
    bind_host: str = "127.0.0.1"
    bind_port: int = 8300
    # HMAC replay protection
    timestamp_window_seconds: int = 300


class ArtifactRules(BaseModel):
    allowed_url_prefix: str


class MigrateSpec(BaseModel):
    command: list[str] | None = None


class RestartSpec(BaseModel):
    command: list[str]


class HealthSpec(BaseModel):
    url: str
    retries: int = 10
    interval_seconds: float = 3


class AppSpec(BaseModel):
    releases_dir: Path
    current_link: Path
    keep_releases: int = 5
    artifact: ArtifactRules
    migrate: MigrateSpec = MigrateSpec()
    restart: RestartSpec
    health: HealthSpec


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_app_registry() -> dict[str, AppSpec]:
    raw = yaml.safe_load(get_settings().apps_config.read_text())
    return {name: AppSpec.model_validate(spec) for name, spec in raw["apps"].items()}


def _secret_key(app_name: str) -> str:
    return "DEPLOYD_SECRET_" + app_name.upper().replace("-", "_")


def get_app_secret(app_name: str) -> str | None:
    """Env var wins over the secrets file, so ops can pin a secret."""
    key = _secret_key(app_name)
    if key in os.environ:
        return os.environ[key]
    path = get_settings().secrets_file
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() == key and not k.lstrip().startswith("#"):
                return v.strip()
    return None


def set_app_secret(app_name: str, secret: str) -> None:
    key = _secret_key(app_name)
    path = get_settings().secrets_file
    lines = []
    if path.exists():
        lines = [ln for ln in path.read_text().splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={secret}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def save_app_registry(registry: dict[str, AppSpec]) -> None:
    data = {"apps": {name: spec.model_dump(mode="json") for name, spec in registry.items()}}
    get_settings().apps_config.write_text(yaml.safe_dump(data, sort_keys=False))
    get_app_registry.cache_clear()
