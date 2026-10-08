import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ShareDocumentRequest(BaseModel):
    email: str
    permission: Literal["view", "review"] = "view"

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        s = value.strip().lower()
        if not EMAIL_PATTERN.match(s):
            raise ValueError("Enter a valid email address.")
        return s


class UpdateShareRequest(BaseModel):
    permission: Literal["view", "review"]


class DocumentShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    shared_with_user_id: UUID
    shared_with_email: str
    shared_with_name: str | None = None
    permission: str
    created_at: datetime | None = None
