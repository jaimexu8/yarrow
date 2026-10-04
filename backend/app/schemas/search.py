from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SearchSnippet(BaseModel):
    region_id: UUID
    page_number: int
    text: str
    match_start: int
    match_end: int


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    snippets: list[SearchSnippet] = Field(default_factory=list)
