from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TableCellMatch(BaseModel):
    table_id: UUID
    region_table_id: UUID
    cell_id: UUID
    row: int
    col: int
    row_span: int
    col_span: int
    is_header: bool
    column_header: str | None = None


class SearchSnippet(BaseModel):
    region_id: UUID
    page_number: int
    text: str
    match_start: int
    match_end: int
    edited: bool = False
    source: Literal["text", "table_cell"] = "text"
    table_cell: TableCellMatch | None = None


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    snippets: list[SearchSnippet] = Field(default_factory=list)
