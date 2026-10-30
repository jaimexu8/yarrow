from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
