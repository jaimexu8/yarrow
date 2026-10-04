"""Logical table models.

Row and column ranges follow the convention written by the worker's
``_build_table``: ``row_end`` and ``col_end`` are **inclusive**, so a part
holding five rows is ``row_start=0, row_end=4``.

``TableCell.row_idx`` in the database is local to its own ``RegionTable``; the
global row inside the logical table is ``row_start + row_idx``. This API always
publishes the global row so that no client has to know that invariant.
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from .common import BBox, StrictModel


class TablePart(StrictModel):
    """One page's worth of a logical table, i.e. a single RegionTable."""

    region_table_id: UUID
    region_id: UUID
    page_number: int
    reading_order: int
    row_start: int
    row_end: int = Field(description="Inclusive")
    col_start: int
    col_end: int = Field(description="Inclusive")


class RowSource(StrictModel):
    """Which page a single global row of a logical table came from"""

    row: int
    page_number: int


class TableCellNode(StrictModel):
    row: int = Field(description="Global row: part.row_start + TableCell.row_idx")
    col: int
    row_span: int
    col_span: int
    text: str | None = None
    is_header: bool = False
    bbox: BBox
    page_number: int
    region_table_id: UUID


class TableNode(StrictModel):
    id: UUID
    title: str | None = None
    row_count: int
    col_count: int
    is_stitched: bool
    has_spans: bool = False
    """Any cell spans more than one row or column.

    GFM cannot express spans, so the markdown export is lossy for these while
    the viewer, which reads ``cells`` directly, is not.
    """
    first_page: int
    last_page: int
    parts: list[TablePart] = Field(default_factory=list)
    row_sources: list[RowSource] = Field(default_factory=list)
    cells: list[TableCellNode] = Field(default_factory=list)


class MergeCandidate(StrictModel):
    """A pair the user may merge, so the UI can enable or disable the action."""

    previous_table_id: UUID
    next_table_id: UUID
    boundary_page: int = Field(description="Page the previous table ends on; the next begins on the following page")


class MergeTablesRequest(StrictModel):
    previous_table_id: UUID
    next_table_id: UUID

    @model_validator(mode="after")
    def _distinct(self) -> "MergeTablesRequest":
        if self.previous_table_id == self.next_table_id:
            raise ValueError("previous_table_id and next_table_id must differ")
        return self


class MergeCandidatesOut(StrictModel):
    """What the merge/split controls should offer for this document."""

    candidates: list[MergeCandidate] = Field(default_factory=list)
    splittable_table_ids: list[UUID] = Field(default_factory=list)
    can_edit: bool


class TableMutationResult(StrictModel):
    """Every table in the document after the edit, so the client re-renders."""

    changed: bool = False
    """False when the request was valid but nothing needed doing, which makes
    merge-all and split-all safely idempotent."""

    table_count_before: int = 0
    table_count_after: int = 0
    tables: list[TableNode] = Field(default_factory=list)
    document_updated_at: datetime | None = None
