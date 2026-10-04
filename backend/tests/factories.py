"""Builders for DocumentTree payloads.

The renderers and the export serializer take a tree and nothing else, so tests
for them need no database, no session and no fixtures -- just these builders.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.schemas.common import BBox
from app.schemas.document import DocumentOut
from app.schemas.parsed import (
    DocumentStats,
    DocumentTree,
    PageNode,
    RegionNode,
)
from app.schemas.table import RowSource, TableCellNode, TableNode, TablePart

BOX = BBox(x0=0.0, y0=0.0, x1=10.0, y1=10.0)


def region(
    region_type: str,
    *,
    text: str | None = None,
    page_number: int = 1,
    reading_order: int = 0,
    table_id: UUID | None = None,
    region_table_id: UUID | None = None,
    is_first_table_part: bool = False,
    caption: str | None = None,
) -> RegionNode:
    return RegionNode(
        id=uuid4(),
        page_number=page_number,
        reading_order=reading_order,
        region_type=region_type,
        bbox=BOX,
        confidence=0.9,
        text=text,
        table_id=table_id,
        region_table_id=region_table_id,
        is_first_table_part=is_first_table_part,
        caption=caption,
    )


def page(page_number: int, *regions: RegionNode) -> PageNode:
    """A page whose regions are numbered in the order they are passed."""
    numbered = []
    for index, item in enumerate(regions):
        numbered.append(
            item.model_copy(update={"reading_order": index, "page_number": page_number})
        )
    return PageNode(
        page_number=page_number,
        width=1275.0,
        height=1650.0,
        status="completed",
        regions=numbered,
    )


def simple_table(
    rows: list[list[str]],
    *,
    table_id: UUID | None = None,
    region_table_id: UUID | None = None,
    page_number: int = 1,
    header: bool = True,
    title: str | None = None,
) -> TableNode:
    """A single-part table whose first row is optionally the header."""
    table_id = table_id or uuid4()
    region_table_id = region_table_id or uuid4()
    cells = [
        TableCellNode(
            row=row_index,
            col=col_index,
            row_span=1,
            col_span=1,
            text=value,
            is_header=header and row_index == 0,
            bbox=BOX,
            page_number=page_number,
            region_table_id=region_table_id,
        )
        for row_index, row in enumerate(rows)
        for col_index, value in enumerate(row)
    ]
    width = max((len(row) for row in rows), default=0)
    return TableNode(
        id=table_id,
        title=title,
        row_count=len(rows),
        col_count=width,
        is_stitched=False,
        has_spans=False,
        first_page=page_number,
        last_page=page_number,
        parts=[
            TablePart(
                region_table_id=region_table_id,
                region_id=uuid4(),
                page_number=page_number,
                reading_order=0,
                row_start=0,
                row_end=max(len(rows) - 1, 0),
                col_start=0,
                col_end=max(width - 1, 0),
            )
        ],
        row_sources=[
            RowSource(row=index, page_number=page_number) for index in range(len(rows))
        ],
        cells=cells,
    )


def stitched_table(
    parts: list[tuple[int, list[list[str]]]],
    *,
    table_id: UUID | None = None,
    title: str | None = None,
) -> tuple[TableNode, list[UUID]]:
    """A table spanning pages.

    ``parts`` is a list of ``(page_number, rows)``. Returns the table and the
    region_table_id of each part, so a test can attach them to regions.
    """
    table_id = table_id or uuid4()
    part_ids = [uuid4() for _ in parts]
    part_nodes: list[TablePart] = []
    cells: list[TableCellNode] = []
    row_sources: list[RowSource] = []

    offset = 0
    width = 0
    for (page_number, rows), part_id in zip(parts, part_ids, strict=True):
        width = max(width, max((len(row) for row in rows), default=0))
        part_nodes.append(
            TablePart(
                region_table_id=part_id,
                region_id=uuid4(),
                page_number=page_number,
                reading_order=len(part_nodes),
                row_start=offset,
                row_end=offset + len(rows) - 1,
                col_start=0,
                col_end=max(len(rows[0]) - 1, 0) if rows else 0,
            )
        )
        for local_row, row in enumerate(rows):
            row_sources.append(
                RowSource(row=offset + local_row, page_number=page_number)
            )
            for col_index, value in enumerate(row):
                cells.append(
                    TableCellNode(
                        row=offset + local_row,
                        col=col_index,
                        row_span=1,
                        col_span=1,
                        text=value,
                        is_header=offset == 0 and local_row == 0,
                        bbox=BOX,
                        page_number=page_number,
                        region_table_id=part_id,
                    )
                )
        offset += len(rows)

    return (
        TableNode(
            id=table_id,
            title=title,
            row_count=offset,
            col_count=width,
            is_stitched=True,
            has_spans=False,
            first_page=parts[0][0],
            last_page=parts[-1][0],
            parts=part_nodes,
            row_sources=row_sources,
            cells=cells,
        ),
        part_ids,
    )


def tree(
    pages: list[PageNode],
    tables: list[TableNode] | None = None,
    *,
    status: str = "completed",
) -> DocumentTree:
    tables = tables or []
    return DocumentTree(
        generated_at=datetime.now(UTC),
        document=DocumentOut(
            id=uuid4(),
            filename="fixture.pdf",
            file_size_bytes=1,
            file_type="application/pdf",
            page_count=len(pages),
            status=status,
        ),
        pages_included=[item.page_number for item in pages],
        pages=pages,
        tables=tables,
        stats=DocumentStats(
            page_count=len(pages),
            region_count=sum(len(item.regions) for item in pages),
            table_count=len(tables),
            stitched_table_count=sum(1 for item in tables if item.is_stitched),
            warning_count=0,
        ),
    )
