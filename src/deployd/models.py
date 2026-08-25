from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class DeployRequest(BaseModel):
    app: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_url: HttpUrl
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggered_by: str = Field(max_length=200)


class DeployStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class DeployAccepted(BaseModel):
    deploy_id: str
    status: DeployStatus = DeployStatus.QUEUED


class DeployDetail(BaseModel):
    deploy_id: str
    app: str
    commit_sha: str
    status: DeployStatus
    created_at: str
    finished_at: str | None
    steps: list[dict]  # [{step, status, started_at, output}]
