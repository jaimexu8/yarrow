"""Request and response models for the v1 API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    # 72 bytes, not characters: that is bcrypt's hard input limit, and it
    # raises on anything longer rather than truncating.
    password: str = Field(min_length=8)
    name: str | None = None

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 bytes")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    name: str | None = None
    is_admin: bool
    storage_used_bytes: int


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    message: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str | None = None
    current_stage: str | None = None
    pages_processed: int | None = None
    total_pages: int | None = None
    error_message: str | None = None


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    status: str | None = None
    width: float | None = None
    height: float | None = None
    error_message: str | None = None


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


class UploadRejected(BaseModel):
    """One file that was not accepted, so a bulk upload can report per file."""

    filename: str
    reason: str


class UploadResponse(BaseModel):
    accepted: list[UploadAccepted] = []
    rejected: list[UploadRejected] = []
