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
    document_count: int


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
