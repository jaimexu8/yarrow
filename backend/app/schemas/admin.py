from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import StrictModel
from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    public_error_message,
)


class AdminAccount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str | None
    email: str
    created_at: datetime | None
    is_admin: bool
    is_active: bool
    document_count: int


class AdminAccountStatusUpdate(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class AdminAccountStatus(BaseModel):
    id: UUID
    is_active: bool
    is_admin: bool


class JobStatusCounts(BaseModel):
    queued: int
    processing: int
    completed: int
    failed: int
    canceled: int


class AdminStats(BaseModel):
    user_count: int
    document_count: int
    storage_used_bytes: int
    jobs_by_status: JobStatusCounts


class AdminJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    filename: str
    owner_id: UUID
    owner_email: str
    owner_name: str | None
    status: str | None = None
    current_stage: str | None = None
    queue_position: int | None = None
    pages_processed: int | None = None
    total_pages: int | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    is_stuck: bool = False
    can_cancel: bool = False
    can_retry: bool = False
    canceled_by_id: UUID | None = None
    cancel_reason: str | None = None
    retry_of_job_id: UUID | None = None

    @model_validator(mode="after")
    def _only_user_facing_errors(self):
        # same sanitizing as job/document responses (us-11)
        self.error_message = public_error_message(
            self.error_message, self.status, DOCUMENT_FAILED_MESSAGE
        )
        return self


class AdminJobCancelRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class AdminJobActionResult(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str
    new_job_id: UUID | None = None
    new_job_status: str | None = None
    scope: Literal["incomplete", "all"] | None = None
    pages: list[int] | None = None


NodeKind = Literal["worker", "inference"]
NodeHealth = Literal["healthy", "degraded", "down", "unknown"]


class AdminNodeStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: NodeKind
    name: str
    hostname: str
    url: str | None = None
    status: NodeHealth
    latency_ms: int | None = None
    last_checked_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, int | str | None] = Field(default_factory=dict)


class NodeStatusSummary(BaseModel):
    healthy: int
    degraded: int
    down: int
    unknown: int


class NodeStatusReport(BaseModel):
    checked_at: datetime
    overall_status: Literal["healthy", "degraded", "down"]
    summary: NodeStatusSummary
    nodes: list[AdminNodeStatus]
