from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

from .config import APP_NAME_PATTERN


class DeployRequest(BaseModel):
    app: str = Field(pattern=APP_NAME_PATTERN)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_url: HttpUrl
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggered_by: str = Field(min_length=1, max_length=200)


class DeployStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class DeployAccepted(BaseModel):
    deploy_id: str
    status: DeployStatus = DeployStatus.QUEUED


class DeployStep(BaseModel):
    step: str
    status: str
    started_at: str
    output: str | None


class DeployDetail(BaseModel):
    deploy_id: str
    app: str
    commit_sha: str
    status: DeployStatus
    created_at: str
    finished_at: str | None
    steps: list[DeployStep]
