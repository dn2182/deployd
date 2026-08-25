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


def get_app_secret(app_name: str) -> str | None:
    """HMAC secret for an app: DEPLOYD_SECRET_<APP_NAME_UPPER_SNAKE>."""
    key = "DEPLOYD_SECRET_" + app_name.upper().replace("-", "_")
    return os.environ.get(key)
