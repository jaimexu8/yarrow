import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from yarrow_db.models import RegionTable, Table
from yarrow_db.models.region import Region


def row_span(region_table: RegionTable) -> int:
    return (region_table.row_end or 0) - (region_table.row_start or 0) + 1


def col_span(region_table: RegionTable) -> int:
    return (region_table.col_end or 0) - (region_table.col_start or 0) + 1


def ordered_parts(session: Session, table: Table) -> list[RegionTable]:
    """Returns the table's parts (RegionTable) in reading order"""
    return (
        session.query(RegionTable)
        .filter(RegionTable.table_id == table.id)
        .order_by(RegionTable.reading_order, RegionTable.id)
        .all()
    )


def is_consecutive(
    session: Session,
    region_table_prev: RegionTable,
    region_table_next: RegionTable,
) -> bool:
    """Checks if two region tables are consecutive.

    For two region tables to be consecutive, the following conditions must be met:
    - They must belong to different tables.
    - The previous region table's region must be at the end of its page.
    - The next region table's region must be at the start of its page.
    - The two pages must be adjacent.
    - Both must have the same number of columns.

    Args:
        session (Session): SQLAlchemy session object.
        region_table_prev (RegionTable): The previous region table.
        region_table_next (RegionTable): The next region table.

    Returns:
        bool: True if the region tables are consecutive, False otherwise.
    """
    
    if region_table_prev.table_id == region_table_next.table_id:
        return False

    region_prev = region_table_prev.region
    region_next = region_table_next.region
    
    if region_prev is None or region_next is None:
        return False

    page_prev = region_prev.page
    page_next = region_next.page
    
    if page_prev is None or page_next is None:
        return False

    # A malformed page can leave any of these null. Treated as "not consecutive"
    if (
        region_prev.reading_order is None
        or region_next.reading_order is None
        or page_prev.page_number is None
        or page_next.page_number is None
    ):
        return False

    prev_max_reading_order = (
        session.query(func.max(Region.reading_order))
        .filter(Region.page_id == region_prev.page_id)
        .scalar()
    )
    next_min_reading_order = (
        session.query(func.min(Region.reading_order))
        .filter(Region.page_id == region_next.page_id)
        .scalar()
    )

    return (
        region_prev.reading_order == prev_max_reading_order
        and region_next.reading_order == next_min_reading_order
        and page_prev.page_number + 1 == page_next.page_number
        and col_span(region_table_prev) == col_span(region_table_next)
    )


def merge_consecutive_tables(session: Session, document_id: uuid.UUID) -> None:
    """
    Merges all consecutive tables into a single table.
    """
    tables = session.query(Table).filter(Table.document_id == document_id).all()

    table_page_intervals: list[dict[str, object]] = []

    for table in tables:
        parts = ordered_parts(session, table)
        if not parts:
            continue

        first_part, last_part = parts[0], parts[-1]
        table_page_intervals.append(
            {
                "table": table,
                "start_page": first_part.region.page.page_number,
                "end_page": last_part.region.page.page_number,
                "start_order": first_part.region.reading_order or 0,
                "first_part_id": first_part.id,
                "last_part_id": last_part.id,
            }
        )

    table_page_intervals.sort(
        key=lambda item: (item["start_page"], item["start_order"])
    )

    for index in range(len(table_page_intervals) - 2, -1, -1):
        table = table_page_intervals[index]["table"]
        next_table = table_page_intervals[index + 1]["table"]

        end_region_table = session.get(
            RegionTable, table_page_intervals[index]["last_part_id"]
        )
        next_start_region_table = session.get(
            RegionTable, table_page_intervals[index + 1]["first_part_id"]
        )
        if end_region_table is None or next_start_region_table is None:
            continue

        if is_consecutive(session, end_region_table, next_start_region_table):
            merge_two_tables(session, table, next_table)


def split_table(session: Session, table: Table) -> list[Table]:
    """Splits one table into a standalone table per region table

    Args:
        session: SQLAlchemy session object
        table: The table to split

    Returns:
        The resulting tables in reading order
    """
    parts = ordered_parts(session, table)

    if len(parts) <= 1:
        # Nothing to split
        table.is_stitched = False
        if parts:
            table.row_count = row_span(parts[0])
            table.col_count = col_span(parts[0])
        session.flush()
        return [table]

    new_tables: list[Table] = []
    new_tables.extend(
        Table(
            id=uuid.uuid4(),
            document_id=table.document_id,
            row_count=row_span(part),
            col_count=col_span(part),
            is_stitched=False,
            title=table.title if index == 0 else None,
        )
        for index, part in enumerate(parts)
    )
    session.add_all(new_tables)
    session.flush()

    # Associates the region table objects to new tables
    for part, new_table in zip(parts, new_tables, strict=True):
        part.table = new_table
        part.reading_order = 0
        part.row_end = part.row_end - part.row_start
        part.row_start = 0

    session.flush()
    session.delete(table)
    session.flush()
    return new_tables


def split_at_gaps(session: Session, table: Table) -> list[Table]:
    """Splits a table wherever its remaining parts skip a page, and repacks the row ranges

    Args:
        session: SQLAlchemy session object
        table: The table to split

    Returns:
        The resulting tables in reading order (empty if the table was deleted)
    """
    parts = ordered_parts(session, table)

    if not parts:
        session.delete(table)
        session.flush()
        return []

    runs: list[list[RegionTable]] = [[parts[0]]]
    for part in parts[1:]:
        if part.region.page.page_number == runs[-1][-1].region.page.page_number + 1:
            runs[-1].append(part)
        else:
            runs.append([part])

    result: list[Table] = []
    for index, run in enumerate(runs):
        if index == 0:
            run_table = table
        else:
            run_table = Table(id=uuid.uuid4(), document_id=table.document_id, title=None)
            session.add(run_table)

        # Repack the run's row ranges so its global rows start at 0 with no holes
        row_offset = 0
        for reading_order, part in enumerate(run):
            rows = row_span(part)
            part.table = run_table
            part.reading_order = reading_order
            part.row_start = row_offset
            part.row_end = row_offset + rows - 1
            row_offset += rows

        run_table.row_count = row_offset
        run_table.col_count = max(col_span(part) for part in run)
        run_table.is_stitched = len(run) > 1
        result.append(run_table)

    session.flush()
    return result


def split_consecutive_tables(session: Session, document_id: uuid.UUID) -> list[Table]:
    """
    Splits all stitched tables into individual tables.

    Returns:
        Every table produced, so a caller can report what changed without
        re-querying.
    """
    stitched = (
        session.query(Table)
        .filter(Table.document_id == document_id, Table.is_stitched.is_(True))
        .order_by(Table.id)
        .all()
    )

    result: list[Table] = []
    for table in stitched:
        result.extend(split_table(session, table))
    return result


def merge_two_tables(session: Session, table_prev: Table, table_next: Table) -> None:
    """
    Merges two specified tables into a single table.

    Consecutiveness is deliberately not checked here. Callers that act on user
    input validate it themselves with is_consecutive().

    Args:
        table_prev: The previous table object to be merged.
        table_next: The next table object to be merged.
    """
    if table_prev.id == table_next.id:
        return

    table_next_region_tables = ordered_parts(session, table_next)
    if not table_next_region_tables:
        session.delete(table_next)
        session.flush()
        return

    row_offset = table_prev.row_count or 0
    next_reading_order = (
        session.query(func.count(RegionTable.id))
        .filter(RegionTable.table_id == table_prev.id)
        .scalar()
        or 0
    )

    for region_table in table_next_region_tables:
        # Transfer the region tables of the next table to the previous table,
        # continuing the reading order and row range after table_prev's own parts.
        region_table.table = table_prev
        region_table.reading_order = next_reading_order
        region_table.row_start += row_offset
        region_table.row_end += row_offset
        next_reading_order += 1
        # TableCell.row_idx stays local to its own region table; the global row
        # is row_start + row_idx, so no offset is applied to the cells here.

    table_prev.is_stitched = True
    table_prev.row_count = row_offset + (table_next.row_count or 0)
    table_prev.col_count = max(table_prev.col_count or 0, table_next.col_count or 0)

    session.delete(table_next)
    session.flush()


def insert_region_table(
    session: Session,
    table: Table,
    region_table: RegionTable,
    insert_after: RegionTable,
) -> None:
    """
    Inserts region_table into table immediately after insert_after.

    Args:
        session: SQLAlchemy session object.
        table: The table to splice region_table into.
        region_table: The region table to move into table.
        insert_after: The region table already in table that region_table should immediately follow.
    """
    if region_table.table_id == table.id:
        return

    orphaned_table = region_table.table
    inserted_rows = row_span(region_table)
    insertion_order = insert_after.reading_order + 1

    later_region_tables = (
        session.query(RegionTable)
        .filter(
            RegionTable.table_id == table.id,
            RegionTable.reading_order >= insertion_order,
        )
        .order_by(RegionTable.reading_order)
        .all()
    )

    for later in later_region_tables:
        later.reading_order += 1
        later.row_start += inserted_rows
        later.row_end += inserted_rows

    region_table.table = table
    region_table.reading_order = insertion_order
    region_table.row_start = insert_after.row_end + 1
    region_table.row_end = region_table.row_start + inserted_rows - 1

    table.row_count = (table.row_count or 0) + inserted_rows
    table.is_stitched = True

    session.flush()

    # Clean up the now-empty table region_table used to belong to, mirroring
    # the anti-orphan check done when a reprocessed page's rows are deleted.
    if orphaned_table is not None and orphaned_table.id != table.id:
        remaining = (
            session.query(RegionTable.id)
            .filter(RegionTable.table_id == orphaned_table.id)
            .first()
        )
        if remaining is None:
            session.delete(orphaned_table)
            session.flush()
