from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class ShareDocumentRequest(BaseModel):
    email: EmailStr
    permission: Literal["view", "review"] = "view"


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
