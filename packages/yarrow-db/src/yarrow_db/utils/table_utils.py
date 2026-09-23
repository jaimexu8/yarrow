from yarrow_db.models import Document, Table, RegionTable
from sqlalchemy.orm import Session
from sqlalchemy import func
import uuid

from yarrow_db.models.region import Region
from yarrow_db.models.table import TableCell


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

def split_consecutive_tables(session: Session) -> None:
    """
    Splits all consecutive tables into individual tables.
    """
    # TODO
    pass

def merge_two_tables(session: Session, table_prev: Table, table_next: Table) -> None:
    """
    Merges two specified tables into a single table.
    
    Args:
        table_prev: The previous table object to be merged.
        table_next: The next table object to be merged.
    """
    if table_prev.id == table_next.id:
        return

    table_next_region_tables = session.query(RegionTable).filter(RegionTable.table_id == table_next.id).all()

    row_offset = table_prev.row_count

    for rt in table_next_region_tables:
        # Transfer the region tables of the next table to the previous table
        rt.table_id = table_prev.id
        table_prev.is_stitched = True

        # Update the table cells of the transferred region table to reflect the row offset
        table_cells = session.query(TableCell).filter(TableCell.region_table_id == rt.id).all()
        for cell in table_cells:
            cell.row_idx += row_offset

    table_prev.row_count = row_offset + table_next.row_count

    session.delete(table_next)
    session.flush()