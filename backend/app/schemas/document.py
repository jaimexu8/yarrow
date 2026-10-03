"""Document, job and upload models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    PAGE_FAILED_MESSAGE,
    public_error_message,
)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str | None = None
    current_stage: str | None = None
    pages_processed: int | None = None
    total_pages: int | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _only_user_facing_errors(self):
        # The worker may store raw exception text here (US-11, NFR-6).
        self.error_message = public_error_message(self.error_message, self.status, DOCUMENT_FAILED_MESSAGE)
        return self


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    status: str | None = None
    width: float | None = None
    height: float | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _only_user_facing_errors(self):
        # The worker may store raw exception text here (US-11, NFR-6).
        self.error_message = public_error_message(self.error_message, self.status, PAGE_FAILED_MESSAGE)
        return self


class ReprocessInfo(BaseModel):
    # some pages to be processed (incomplete) or all of them (all)
    scope: Literal["incomplete", "all"]

    # Its job was lost mid-run (e.g. a worker crashed)
    interrupted: bool = False

    # Pages that would be processed, or None for all of them
    pages: int | None = None

    # The first few unfinished page numbers, for display ("incomplete" only).
    page_numbers: list[int] = Field(default_factory=list)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    file_size_bytes: int
    file_type: str
    page_count: int | None = None
    status: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    # Filled in by the endpoint (see app.services.reprocess); not a column.
    reprocess: ReprocessInfo | None = None

    @model_validator(mode="after")
    def _only_user_facing_errors(self):
        # The worker may store raw exception text here (US-11, NFR-6).
        self.error_message = public_error_message(self.error_message, self.status, DOCUMENT_FAILED_MESSAGE)
        return self


class DocumentRename(BaseModel):
    """A new display name (US-39). Storage is keyed by id, so only the name
    shown to the user changes."""

    filename: str = Field(max_length=255)

    @field_validator("filename")
    @classmethod
    def _sensible_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        if any(char in value for char in "/\\") or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Name cannot contain slashes or control characters")
        return value


class DocumentDetail(DocumentOut):
    jobs: list[JobOut] = []
    pages: list[PageOut] = []


class UploadAccepted(BaseModel):
    """One accepted file. Bulk upload returns a list of these (backlog #8)."""

    document_id: UUID
    job_id: UUID
    task_id: str
    filename: str
    file_size_bytes: int
    # The document's processing status after the upload: normally "queued";
    # "failed" when the file was stored but processing could not be started,
    # with the reason in ``message``.
    status: str = "queued"
    message: str | None = None


class UploadRejected(BaseModel):
    """One file that was not accepted, so a bulk upload can report per file."""

    filename: str
    reason: str
    # True when the problem was on our side and may clear up (e.g. storage
    # briefly unavailable), so the client can offer "Retry". False for
    # problems with the file itself, which retrying cannot fix.
    retryable: bool = False


class UploadResponse(BaseModel):
    accepted: list[UploadAccepted] = []
    rejected: list[UploadRejected] = []
