import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from yarrow_db.models import RegionTable, Table
from yarrow_db.models.region import Region


def is_consecutive(session: Session, region_table_prev: RegionTable, region_table_next: RegionTable) -> bool:
    """
    Checks if two region tables are consecutive.
    
    For two region tables to be consecutive, the following conditions must be met:
    - The previous region table's region must be at the end of its page.
    - The next region table's region must be at the start of its page.
    - The previous and next region tables must have the same column end.

    Args:
        session (Session): SQLAlchemy session object.
        region_table_prev (RegionTable): The previous region table.
        region_table_next (RegionTable): The next region table.

    Returns:
        bool: True if the region tables are consecutive, False otherwise.
    """
    
    # Get the region objects of each region table
    region_prev = region_table_prev.region
    region_next = region_table_next.region
    
    prev_max_reading_order = session.query(func.max(Region.reading_order)).filter(Region.page_id == region_prev.page_id).scalar()
    next_min_reading_order = session.query(func.min(Region.reading_order)).filter(Region.page_id == region_next.page_id).scalar()
    prev_col_count = region_table_prev.col_end - region_table_prev.col_start
    next_col_count = region_table_next.col_end - region_table_next.col_start
    
    return (
        region_prev.reading_order == prev_max_reading_order and
        region_next.reading_order == next_min_reading_order and
        region_prev.page.page_number + 1 == region_next.page.page_number and
        prev_col_count == next_col_count
    )


def merge_consecutive_tables(session: Session, document_id: uuid.UUID) -> None:
    """
    Merges all consecutive tables into a single table.
    """
        
    # Get all table objects associated with the document
    tables = session.query(Table).filter(Table.document_id == document_id).all()
    
    table_page_intervals: list[dict[str, object]] = []
    
    # For each table object, get its region table with highest reading order (rt_high) and lowest reading order (rt_low)
    for table in tables:
        max_order = (
            session.query(func.max(RegionTable.reading_order))
            .filter(RegionTable.table_id == table.id)
            .scalar()
        )

        min_order = (
            session.query(func.min(RegionTable.reading_order))
            .filter(RegionTable.table_id == table.id)
            .scalar()
        )

        rt_high = (
            session.query(RegionTable)
            .filter(
                RegionTable.table_id == table.id,
                RegionTable.reading_order == max_order,
            )
            .first()
        )

        rt_low = (
            session.query(RegionTable)
            .filter(
                RegionTable.table_id == table.id,
                RegionTable.reading_order == min_order,
            )
            .first()
        )
    
        table_page_intervals.append({
            "table": table,
            "start_page": rt_low.region.page.page_number,
            "end_page": rt_high.region.page.page_number,
            "rt_high_id": rt_high.id,
            "rt_low_id": rt_low.id,
        })

    # Sort page intervals
    table_page_intervals.sort(key=lambda x: (x["start_page"], x["end_page"]))

    # Traverse page intervals, check if two are consecutive, if so, merge them.
    for i in range(len(table_page_intervals) - 2, -1, -1):
        table = table_page_intervals[i]["table"]
        end_region_table_id = table_page_intervals[i]["rt_high_id"]
        
        next_table = table_page_intervals[i + 1]["table"]
        next_start_region_table_id = table_page_intervals[i + 1]["rt_low_id"]

        end_region_table = session.query(RegionTable).get(end_region_table_id)
        next_start_region_table = session.query(RegionTable).get(next_start_region_table_id)
        
        # check if the current table and the next table are consecutive
        if is_consecutive(session, end_region_table, next_start_region_table):
            merge_two_tables(session, table, next_table)

def split_consecutive_tables(session: Session, document_id: uuid.UUID) -> None:
    """
    Splits all consecutive tables into individual tables.
    """
    # TODO
    
    # Get all the stitched tables associated with the document
    tables = session.query(Table).filter(Table.document_id == document_id, Table.is_stitched == True).all()
    # Get all the region tables associated with the stitched tables
    region_tables = session.query(RegionTable).filter(RegionTable.table_id.in_([t.id for t in tables])).all()
    
    # For each region table, create its own table object
    new_tables: list[Table] = []
    for rt in region_tables:
        new_table = Table(
            id=uuid.uuid4(),
            document_id=document_id,
            is_stitched=False,
            row_count=(rt.row_end - rt.row_start),
            col_count=(rt.col_end - rt.col_start),
            title=rt.table.title if rt.table else None
        )
        new_tables.append(new_table)
        rt.table_id = new_table.id

        # Reset the row range to be 0-based within the new standalone table.
        # TableCell.row_idx is already local to its own region table (the
        # global row is row_start + row_idx), so cells need no adjustment.
        row_offset = rt.row_start
        rt.row_end -= row_offset
        rt.row_start = 0

    session.add_all(new_tables)

    # Remove the stitched tables
    for table in tables:
        session.delete(table)
    session.flush()

def merge_two_tables(session: Session, table_prev: Table, table_next: Table) -> None:
    """
    Merges two specified tables into a single table.
    
    Args:
        table_prev: The previous table object to be merged.
        table_next: The next table object to be merged.
    """
    if table_prev.id == table_next.id:
        return

    table_next_region_tables = (
        session.query(RegionTable)
        .filter(RegionTable.table_id == table_next.id)
        .order_by(RegionTable.reading_order)
        .all()
    )

    row_offset = table_prev.row_count
    next_reading_order = session.query(func.count(RegionTable.id)).filter(RegionTable.table_id == table_prev.id).scalar()

    for rt in table_next_region_tables:
        # Transfer the region tables of the next table to the previous table,
        # continuing the reading order and row range after table_prev's own parts.
        rt.table = table_prev
        rt.reading_order = next_reading_order
        rt.row_start += row_offset
        rt.row_end += row_offset
        table_prev.is_stitched = True
        next_reading_order += 1
        # TableCell.row_idx stays local to its own region table; the global row
        # is row_start + row_idx, so no offset is applied to the cells here.

    table_prev.row_count = row_offset + table_next.row_count

    session.delete(table_next)
    session.flush()