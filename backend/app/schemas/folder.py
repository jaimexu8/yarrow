"""Folder request and response models (US-103)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FolderName(BaseModel):
    """A folder display name. Trimmed; empty names are rejected."""

    name: str = Field(max_length=255)

    @field_validator("name")
    @classmethod
    def _sensible_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty")
        return value


class FolderCreate(FolderName):
    pass


class FolderRename(FolderName):
    pass


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime | None = None


class DocumentFolderUpdate(BaseModel):
    """Move a document into a folder, or unfile it with null."""

    folder_id: UUID | None
