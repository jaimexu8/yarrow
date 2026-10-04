"""The canonical parsed-document representation.

One tree feeds three consumers: the viewer payload, the JSON export, and the
markdown renderer's input. Keeping them on one model is what stops the export
and the on-screen view from drifting apart.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .common import BBox, StrictModel
from .document import DocumentOut
from .table import TableNode

SCHEMA_VERSION = 1


class WarningNode(StrictModel):
    warning_type: str | None = None
    message: str | None = None


class RegionNode(StrictModel):
    """One extracted block, in reading order within its page."""

    id: UUID  # Region.id
    page_number: int  # Region.page.page_number
    reading_order: int  # Region.reading_order
    region_type: str | None = None  # Region.region_type
    bbox: BBox  # x0, y0, x1, y1
    confidence: float | None = None  # Region.confidence
    text: str | None = None  # RegionText.text_content. None for tables and figures
    table_id: UUID | None = None  # Set when the region is part of a table
    region_table_id: UUID | None = None  # Set when the region corresponds to a region table
    is_first_table_part: bool = False  # True if this region is the first region of a table
    image_key: str | None = None  # Key to the image of this region, if available
    caption: str | None = None  # Caption text for this region, if available


class PageNode(StrictModel):
    page_number: int
    width: float | None = None
    height: float | None = None
    status: str | None = None
    error_message: str | None = None
    regions: list[RegionNode] = Field(default_factory=list)
    warnings: list[WarningNode] = Field(default_factory=list)


class DocumentStats(StrictModel):
    page_count: int
    region_count: int
    table_count: int
    stitched_table_count: int
    warning_count: int


class DocumentTree(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    generated_at: datetime
    document: DocumentOut
    pages_included: list[int] = Field(default_factory=list)
    pages: list[PageNode] = Field(default_factory=list)
    tables: list[TableNode] = Field(default_factory=list)  # Always document-scoped, even under a page filter
    stats: DocumentStats
