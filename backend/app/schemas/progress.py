from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

PageStatus = Literal["queued", "processing", "completed", "failed", "canceled"]


class PageProgress(BaseModel):
    page_number: int
    status: PageStatus | None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentProgress(BaseModel):
    document_id: UUID
    document_status: str | None
    job_id: UUID | None
    job_status: str | None
    current_stage: str | None
    pages_total: int
    pages_completed: int
    pages_failed: int
    progress_percent: int
    updated_at: datetime | None
    pages: list[PageProgress]
