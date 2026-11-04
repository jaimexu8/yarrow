"""Resumable upload sessions (US-80)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"


class UploadSessionCreate(BaseModel):
    """What the browser knows about a file before sending any of it."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(max_length=255)
    total_size_bytes: int = Field(gt=0)
    chunk_size_bytes: int = Field(gt=0)
    # Optional whole-file checksum, compared with the assembled file.
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # The US-31 upload run this file belongs to.
    batch_id: UUID | None = None

    @field_validator("filename")
    @classmethod
    def _sensible_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Name cannot contain control characters")
        return value

    @field_validator("sha256")
    @classmethod
    def _lowercase(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class UploadFinalize(BaseModel):
    """Optional finalize body (US-80)."""

    model_config = ConfigDict(extra="forbid")

    # The browser's sha256 of its whole local file; the assembled document
    # must match it.
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # The US-31 run finishing this upload, when it was resumed in a later
    # run than the one that started it.
    batch_id: UUID | None = None
    # "Upload anyway" (US-79): store the file even though the caller already
    # has an identical document. Without it such a finalize is refused with
    # 409 UPLOAD_DUPLICATE and the session stays open for a second finalize.
    allow_duplicate: bool = False

    @field_validator("sha256")
    @classmethod
    def _lowercase(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class UploadSessionOut(BaseModel):
    """A session's state. ``received_chunks`` is what the browser resumes
    from: every index listed is stored on the server and never needs to be
    sent again."""

    id: UUID
    filename: str
    total_size_bytes: int
    chunk_size_bytes: int
    chunk_count: int
    received_chunks: list[int]
    # sha256 of each confirmed chunk, in the order of received_chunks. The
    # browser checks its local file against these before resuming, so a
    # different file never continues someone else's upload.
    received_checksums: list[str]
    received_bytes: int
    # "processing" while receiving chunks, "completed" once finalized,
    # "failed" when the file failed validation (reason in error_message).
    status: str
    error_message: str | None = None
    expires_at: datetime
    batch_id: UUID | None = None
    document_id: UUID | None = None
    # sha256 of the stored document, once completed.
    file_sha256: str | None = None


class ChunkReceipt(BaseModel):
    """The server's confirmation that one chunk is stored."""

    index: int
    size_bytes: int
    sha256: str
    # True when this index was already stored with the same bytes, i.e. the
    # request was a retry and nothing new was written.
    already_received: bool
    received_bytes: int
    received_count: int
