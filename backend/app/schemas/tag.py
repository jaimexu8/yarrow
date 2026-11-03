"""Per-user document tags (US-105)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TAG_NAME_LENGTH = 40


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class TagListItem(TagOut):
    document_count: int = 0


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _sensible_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tag name cannot be empty")
        if len(value) > MAX_TAG_NAME_LENGTH:
            raise ValueError(
                f"Tag name must be {MAX_TAG_NAME_LENGTH} characters or fewer"
            )
        return value
