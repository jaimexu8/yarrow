"""Assemble the parsed graph into a DocumentTree"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionImage,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    Warning,
)

from app.deps import DocumentAccess
from app.schemas.common import BBox
from app.schemas.parsed import (
    DocumentStats,
    DocumentTree,
    PageNode,
    RegionNode,
    WarningNode,
)
from app.schemas.table import (
    RowSource,
    TableCellNode,
    TableNode,
    TablePart,
)
from app.services.reprocess import documents_out


def _bbox(row: Region | TableCell) -> BBox:
    """Bounding boxes are stored as four columns, never null (the parser writes 0)."""
    return BBox(
        x0=row.x0 or 0.0,
        y0=row.y0 or 0.0,
        x1=row.x1 or 0.0,
        y1=row.y1 or 0.0,
    )


class _TableAssembly:
    """Mutable working state for one logical table."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.parts: list[tuple[RegionTable, int]] = []  # (region_table, page_number)
        self.cells: list[TableCellNode] = []
        self.has_spans = False


async def load_document_tree(
    db: AsyncSession,
    access: DocumentAccess,
    page: int | None = None,
    include_cells: bool = True,
    include_warnings: bool = True,
) -> DocumentTree:
    """Load one document's parsed graph.

    Args:
        db: Async session.
        access: The resolved document and the caller's permission.
        page: Restrict pages to this 1-based page number. Tables are still
            returned whole.
        include_cells: Whether to include the individual table cells in the output.
        include_warnings: Whether to include warnings associated with the document in the output.
    """
    document: Document = access.document
    document_id = document.id

    page_ids: Select = select(Page.id).where(Page.document_id == document_id)

    if page is not None:
        page_ids = page_ids.where(Page.page_number == page)

    region_ids: Select = select(Region.id).where(Region.page_id.in_(page_ids))

    table_ids: Select = select(RegionTable.table_id).where(RegionTable.region_id.in_(region_ids))

    pages_query = select(Page).where(Page.document_id == document_id)

    if page is not None:
        pages_query = pages_query.where(Page.page_number == page)

    page_rows = (await db.execute(pages_query.order_by(Page.page_number))).scalars().all()

    region_rows = (
        (await db.execute(select(Region).where(Region.page_id.in_(page_ids)).order_by(Region.page_id, Region.reading_order, Region.id)))
        .scalars()
        .all()
    )

    text_rows = (await db.execute(select(RegionText).where(RegionText.region_id.in_(region_ids)))).scalars().all()

    image_rows = (await db.execute(select(RegionImage).where(RegionImage.region_id.in_(region_ids)))).scalars().all()

    warning_rows: list[Warning] = []
    if include_warnings:
        warning_rows = list((await db.execute(select(Warning).where(Warning.page_id.in_(page_ids)))).scalars().all())

    table_rows = (await db.execute(select(Table).where(Table.id.in_(table_ids)))).scalars().all()

    part_rows = (
        await db.execute(
            select(RegionTable, Page.page_number)
            .join(Region, RegionTable.region_id == Region.id)
            .join(Page, Region.page_id == Page.id)
            .where(RegionTable.table_id.in_(table_ids))
            .order_by(RegionTable.table_id, RegionTable.reading_order, RegionTable.id)
        )
    ).all()

    cell_rows: list[TableCell] = []
    if include_cells:
        cell_rows = list(
            (
                await db.execute(
                    select(TableCell)
                    .where(TableCell.region_table_id.in_(select(RegionTable.id).where(RegionTable.table_id.in_(table_ids))))
                    .order_by(TableCell.region_table_id, TableCell.row_idx, TableCell.col_idx)
                )
            )
            .scalars()
            .all()
        )

    # Start stitching together the document tree components

    # Index related records by their parent IDs for efficient tree assembly.
    page_number_by_id = {row.id: row.page_number for row in page_rows}
    text_by_region: dict[uuid.UUID, RegionText] = {row.region_id: row for row in text_rows}
    image_by_region: dict[uuid.UUID, RegionImage] = {row.region_id: row for row in image_rows}

    # Group warnings under their corresponding page nodes.
    warnings_by_page: dict[uuid.UUID, list[WarningNode]] = defaultdict(list)
    for warning in warning_rows:
        warnings_by_page[warning.page_id].append(WarningNode(warning_type=warning.warning_type, message=warning.message))

    # Prepare an assembly for each table and attach its page-specific parts.
    assemblies: dict[uuid.UUID, _TableAssembly] = {row.id: _TableAssembly(row) for row in table_rows}
    part_by_id: dict[uuid.UUID, RegionTable] = {}
    part_page_number: dict[uuid.UUID, int] = {}
    part_by_region: dict[uuid.UUID, RegionTable] = {}
    for region_table, part_page in part_rows:
        part_by_id[region_table.id] = region_table
        part_page_number[region_table.id] = part_page
        part_by_region[region_table.region_id] = region_table
        assembly = assemblies.get(region_table.table_id)
        if assembly is not None:
            assembly.parts.append((region_table, part_page))

    # Mark the first part of each table so clients can identify where it begins.
    first_part_ids: set[uuid.UUID] = {assembly.parts[0][0].id for assembly in assemblies.values() if assembly.parts}
    _attach_cells(assemblies, part_by_id, part_page_number, cell_rows)

    # Convert database regions into API nodes and group them by page.
    regions_by_page: dict[uuid.UUID, list[RegionNode]] = defaultdict(list)
    for region in region_rows:
        part = part_by_region.get(region.id)
        text = text_by_region.get(region.id)
        image = image_by_region.get(region.id)
        regions_by_page[region.page_id].append(
            RegionNode(
                id=region.id,
                page_number=page_number_by_id[region.page_id],
                reading_order=region.reading_order or 0,
                region_type=region.region_type,
                bbox=_bbox(region),
                confidence=region.confidence,
                text=text.text_content if text is not None else None,
                table_id=part.table_id if part is not None else None,
                region_table_id=part.id if part is not None else None,
                is_first_table_part=part is not None and part.id in first_part_ids,
                image_key=image.image_key if image is not None else None,
                caption=image.caption if image is not None else None,
            )
        )

    # Assemble each page with its regions and warnings.
    page_nodes = [
        PageNode(
            page_number=row.page_number,
            width=row.width,
            height=row.height,
            status=row.status,
            error_message=row.error_message,
            regions=regions_by_page.get(row.id, []),
            warnings=warnings_by_page.get(row.id, []),
        )
        for row in page_rows
    ]

    # Build table nodes in document reading order.
    table_nodes = [_build_table_node(assembly) for assembly in sorted(assemblies.values(), key=_table_sort_key)]

    # Return the complete document tree together with summary statistics.
    return DocumentTree(
        generated_at=datetime.now(UTC),
        document=(await documents_out(db, [document]))[0],
        pages_included=[row.page_number for row in page_rows],
        pages=page_nodes,
        tables=table_nodes,
        stats=DocumentStats(
            page_count=len(page_nodes),
            region_count=len(region_rows),
            table_count=len(table_nodes),
            stitched_table_count=sum(bool(node.is_stitched) for node in table_nodes),
            warning_count=len(warning_rows),
        ),
    )


async def load_tables(
    db: AsyncSession,
    access: DocumentAccess,
    include_cells: bool = False,
) -> list[TableNode]:
    """Loads the tables associated with the document provided by the given document access"""

    # Use the authorized document ID to scope the table query.
    document_id = access.document.id
    table_ids: Select = select(Table.id).where(Table.document_id == document_id)

    table_rows = (await db.execute(select(Table).where(Table.document_id == document_id))).scalars().all()
    part_rows = (
        await db.execute(
            select(RegionTable, Page.page_number)
            .join(Region, RegionTable.region_id == Region.id)
            .join(Page, Region.page_id == Page.id)
            .where(RegionTable.table_id.in_(table_ids))
            .order_by(RegionTable.table_id, RegionTable.reading_order, RegionTable.id)
        )
    ).all()

    assemblies: dict[uuid.UUID, _TableAssembly] = {row.id: _TableAssembly(row) for row in table_rows}
    part_by_id: dict[uuid.UUID, RegionTable] = {}
    part_page_number: dict[uuid.UUID, int] = {}
    for region_table, part_page in part_rows:
        part_by_id[region_table.id] = region_table
        part_page_number[region_table.id] = part_page
        assembly = assemblies.get(region_table.table_id)
        if assembly is not None:
            assembly.parts.append((region_table, part_page))

    if include_cells:
        cell_rows = (
            (
                await db.execute(
                    select(TableCell)
                    .where(TableCell.region_table_id.in_(select(RegionTable.id).where(RegionTable.table_id.in_(table_ids))))
                    .order_by(TableCell.region_table_id, TableCell.row_idx, TableCell.col_idx)
                )
            )
            .scalars()
            .all()
        )
        _attach_cells(assemblies, part_by_id, part_page_number, cell_rows)
    else:
        spanning = (
            (
                await db.execute(
                    select(RegionTable.table_id)
                    .join(TableCell, TableCell.region_table_id == RegionTable.id)
                    .where(
                        RegionTable.table_id.in_(table_ids),
                        (TableCell.row_span > 1) | (TableCell.col_span > 1),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        for spanning_table_id in spanning:
            assembly = assemblies.get(spanning_table_id)
            if assembly is not None:
                assembly.has_spans = True

    # Return table nodes ordered by their first page and reading order.
    return [_build_table_node(assembly) for assembly in sorted(assemblies.values(), key=_table_sort_key)]


def _table_sort_key(assembly: _TableAssembly) -> tuple[int, int]:
    """Document order: first page, then reading order on that page."""
    if not assembly.parts:
        return (0, 0)
    region_table, part_page = assembly.parts[0]
    return (part_page, region_table.reading_order or 0)


def _attach_cells(
    assemblies: dict[uuid.UUID, _TableAssembly],
    part_by_id: dict[uuid.UUID, RegionTable],
    part_page_number: dict[uuid.UUID, int],
    cell_rows: list[TableCell],
) -> None:
    for cell in cell_rows:
        region_table = part_by_id.get(cell.region_table_id)
        if region_table is None:
            continue
        assembly = assemblies.get(region_table.table_id)
        if assembly is None:
            continue
        row_span = cell.row_span or 1
        col_span = cell.col_span or 1
        if row_span > 1 or col_span > 1:
            assembly.has_spans = True

        global_row = (region_table.row_start or 0) + (cell.row_idx or 0)
        assembly.cells.append(
            TableCellNode(
                row=global_row,
                col=cell.col_idx or 0,
                row_span=row_span,
                col_span=col_span,
                text=cell.text_content,
                is_header=bool(cell.is_header),
                bbox=_bbox(cell),
                page_number=part_page_number[region_table.id],
                region_table_id=region_table.id,
            )
        )


def _build_table_node(assembly: _TableAssembly) -> TableNode:
    table = assembly.table
    parts = assembly.parts

    part_nodes = [
        TablePart(
            region_table_id=region_table.id,
            region_id=region_table.region_id,
            page_number=part_page,
            reading_order=region_table.reading_order or 0,
            row_start=region_table.row_start or 0,
            row_end=region_table.row_end or 0,
            col_start=region_table.col_start or 0,
            col_end=region_table.col_end or 0,
        )
        for region_table, part_page in parts
    ]

    # Record which page contributes each row of the assembled table.
    row_sources = [RowSource(row=row, page_number=part.page_number) for part in part_nodes for row in range(part.row_start, part.row_end + 1)]

    # Derive dimensions from the available parts and cells to handle incomplete metadata.
    derived_rows = sum((region_table.row_end or 0) - (region_table.row_start or 0) + 1 for region_table, _ in parts)
    derived_cols = max(
        (cell.col + cell.col_span for cell in assembly.cells),
        default=0,
    )
    part_cols = max(
        ((part.col_end - part.col_start + 1) for part in part_nodes),
        default=0,
    )

    # Use the part page numbers to determine the table's page range.
    page_numbers = [part.page_number for part in part_nodes]

    return TableNode(
        id=table.id,
        title=table.title,
        row_count=max(table.row_count or 0, derived_rows),
        col_count=max(table.col_count or 0, derived_cols, part_cols),
        is_stitched=bool(table.is_stitched),
        has_spans=assembly.has_spans,
        first_page=min(page_numbers, default=0),
        last_page=max(page_numbers, default=0),
        parts=part_nodes,
        row_sources=row_sources,
        cells=assembly.cells,
    )
