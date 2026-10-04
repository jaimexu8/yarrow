"""Merge and split behaviour for logical tables, against a live database"""

import uuid

import pytest
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionTable,
    Table,
    TableCell,
    User,
)
from yarrow_db.session import session_scope
from yarrow_db.utils.table_utils import (
    is_consecutive,
    merge_consecutive_tables,
    ordered_parts,
    split_at_gaps,
    split_consecutive_tables,
    split_table,
)

pytestmark = pytest.mark.integration

PART_ROWS = [17, 15, 17, 19]
COL_COUNT = 3
TITLE = "Table 1: Quarterly figures"


@pytest.fixture
def graph():
    """A four-page document, one single-part table per page, title on the first.

    Yields the document id. Rows are removed in explicit foreign-key order on
    teardown, because the schema has no ON DELETE behaviour.
    """
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Sets up the initial database state
    with session_scope() as session:
        session.add(
            User(
                id=user_id,
                email=f"table-utils-{user_id}@example.test",
                name="table utils fixture",
                hashed_password="not-a-real-hash",
            )
        )
        session.add(
            Document(
                id=document_id,
                owner_id=user_id,
                filename="table_utils_fixture.pdf",
                file_size_bytes=1,
                file_type="application/pdf",
                storage_key=f"test/{document_id}",
                page_count=len(PART_ROWS),
                status="completed",
            )
        )
        session.flush()

        for index, rows in enumerate(PART_ROWS):
            page_id = uuid.uuid4()
            region_id = uuid.uuid4()
            table_id = uuid.uuid4()
            region_table_id = uuid.uuid4()

            session.add(
                Page(
                    id=page_id,
                    document_id=document_id,
                    page_number=index + 1,
                    width=1275.0,
                    height=1650.0,
                    status="completed",
                )
            )

            session.add(
                Region(
                    id=region_id,
                    page_id=page_id,
                    reading_order=0,
                    region_type="table",
                    x0=0.0,
                    y0=0.0,
                    x1=100.0,
                    y1=100.0,
                )
            )
            session.add(
                Table(
                    id=table_id,
                    document_id=document_id,
                    row_count=rows,
                    col_count=COL_COUNT,
                    is_stitched=False,
                    title=TITLE if index == 0 else None,
                )
            )
            session.add(
                RegionTable(
                    id=region_table_id,
                    region_id=region_id,
                    table_id=table_id,
                    reading_order=0,
                    row_start=0,
                    row_end=rows - 1,  # inclusive
                    col_start=0,
                    col_end=COL_COUNT - 1,
                )
            )
            for row in range(rows):
                for col in range(COL_COUNT):
                    session.add(
                        TableCell(
                            id=uuid.uuid4(),
                            region_table_id=region_table_id,
                            row_idx=row,
                            col_idx=col,
                            row_span=1,
                            col_span=1,
                            text_content=f"p{index + 1}r{row}c{col}",
                            is_header=row == 0,
                        )
                    )

    yield document_id

    # Cleans up the database after the test
    with session_scope() as session:
        page_ids = [row[0] for row in session.query(Page.id).filter(Page.document_id == document_id)]
        region_ids = [row[0] for row in session.query(Region.id).filter(Region.page_id.in_(page_ids))]
        region_table_ids = [row[0] for row in session.query(RegionTable.id).filter(RegionTable.region_id.in_(region_ids))]
        if region_table_ids:
            session.query(TableCell).filter(TableCell.region_table_id.in_(region_table_ids)).delete(synchronize_session=False)
            session.query(RegionTable).filter(RegionTable.id.in_(region_table_ids)).delete(synchronize_session=False)
        if region_ids:
            session.query(Region).filter(Region.id.in_(region_ids)).delete(synchronize_session=False)
        session.query(Page).filter(Page.document_id == document_id).delete(synchronize_session=False)
        session.query(Table).filter(Table.document_id == document_id).delete(synchronize_session=False)
        session.query(Document).filter(Document.id == document_id).delete(synchronize_session=False)
        session.query(User).filter(User.id == user_id).delete(synchronize_session=False)


def _tables(session, document_id):
    return session.query(Table).filter(Table.document_id == document_id).order_by(Table.row_count, Table.id).all()


def _parts_in_document_order(session, document_id):
    """RegionTable ordered by page and reading order"""
    return (
        session.query(RegionTable)
        .join(Region, RegionTable.region_id == Region.id)
        .join(Page, Region.page_id == Page.id)
        .join(Table, RegionTable.table_id == Table.id)
        .filter(Table.document_id == document_id)
        .order_by(Page.page_number, RegionTable.reading_order)
        .all()
    )


class TestMergeAll:
    def test_merges_into_one_contiguous_table(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        with session_scope() as session:
            tables = _tables(session, graph)
            assert len(tables) == 1

            merged = tables[0]
            assert merged.row_count == sum(PART_ROWS)
            assert merged.col_count == COL_COUNT
            assert merged.is_stitched is True
            assert merged.title == TITLE

            parts = ordered_parts(session, merged)
            assert [part.reading_order for part in parts] == [0, 1, 2, 3]

            # Contiguous, no gap and no overlap: this is what the off-by-one
            # bugs in this module used to break.
            expected_start = 0
            for part, rows in zip(parts, PART_ROWS, strict=True):
                assert part.row_start == expected_start
                assert part.row_end == expected_start + rows - 1
                expected_start += rows
            assert expected_start == merged.row_count

    def test_every_global_row_is_claimed_exactly_once(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        with session_scope() as session:
            merged = _tables(session, graph)[0]
            claimed: list[int] = []
            for part in ordered_parts(session, merged):
                claimed.extend(range(part.row_start, part.row_end + 1))
            assert sorted(claimed) == list(range(merged.row_count))

    def test_is_idempotent(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        with session_scope() as session:
            tables = _tables(session, graph)
            assert len(tables) == 1
            assert tables[0].row_count == sum(PART_ROWS)


class TestSplitAll:
    def test_restores_the_original_shapes(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)
        with session_scope() as session:
            split_consecutive_tables(session, graph)

        with session_scope() as session:
            tables = _tables(session, graph)
            assert len(tables) == len(PART_ROWS)
            assert sorted(table.row_count for table in tables) == sorted(PART_ROWS)
            assert all(table.col_count == COL_COUNT for table in tables)
            assert all(table.is_stitched is False for table in tables)

            # The title belongs to the page it was found on, not to all four.
            assert sum(1 for table in tables if table.title == TITLE) == 1

            for part in _parts_in_document_order(session, graph):
                assert part.row_start == 0
                assert part.col_start == 0
                assert part.col_end == COL_COUNT - 1

    def test_merge_then_split_is_a_round_trip(self, graph):
        with session_scope() as session:
            before = sorted(table.row_count for table in _tables(session, graph))

        with session_scope() as session:
            merge_consecutive_tables(session, graph)
        with session_scope() as session:
            split_consecutive_tables(session, graph)

        with session_scope() as session:
            after = sorted(table.row_count for table in _tables(session, graph))
        assert after == before

    def test_cells_keep_their_local_rows(self, graph):
        """Splitting must not touch cells; the global row comes from the part."""
        with session_scope() as session:
            merge_consecutive_tables(session, graph)
        with session_scope() as session:
            split_consecutive_tables(session, graph)

        with session_scope() as session:
            for part in _parts_in_document_order(session, graph):
                rows = [row[0] for row in session.query(TableCell.row_idx).filter(TableCell.region_table_id == part.id)]
                assert min(rows) == 0
                assert max(rows) == part.row_end - part.row_start


class TestSplitOneTable:
    def test_single_part_table_is_left_alone_but_unflagged(self, graph):
        with session_scope() as session:
            table = _tables(session, graph)[0]
            table.is_stitched = True  # a leftover flag from an earlier edit
            table_id = table.id

        with session_scope() as session:
            table = session.get(Table, table_id)
            result = split_table(session, table)
            assert [item.id for item in result] == [table_id]

        with session_scope() as session:
            assert session.get(Table, table_id).is_stitched is False

    def test_splits_only_the_named_table(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        with session_scope() as session:
            merged_id = _tables(session, graph)[0].id
            split_table(session, session.get(Table, merged_id))

        with session_scope() as session:
            assert session.get(Table, merged_id) is None
            assert len(_tables(session, graph)) == len(PART_ROWS)


class TestIsConsecutive:
    def test_rejects_two_parts_of_the_same_table(self, graph):
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        with session_scope() as session:
            parts = ordered_parts(session, _tables(session, graph)[0])
            assert is_consecutive(session, parts[0], parts[1]) is False

    def test_rejects_mismatched_column_counts(self, graph):
        with session_scope() as session:
            parts = _parts_in_document_order(session, graph)
            parts[1].col_end = COL_COUNT  # one column wider than its neighbour

        with session_scope() as session:
            parts = _parts_in_document_order(session, graph)
            assert is_consecutive(session, parts[0], parts[1]) is False

    def test_rejects_non_adjacent_pages(self, graph):
        with session_scope() as session:
            parts = _parts_in_document_order(session, graph)
            assert is_consecutive(session, parts[0], parts[2]) is False

    def test_rejects_a_part_that_is_not_last_on_its_page(self, graph):
        """A footer after the table defeats the page-extremity rule."""
        with session_scope() as session:
            first = _parts_in_document_order(session, graph)[0]
            session.add(
                Region(
                    id=uuid.uuid4(),
                    page_id=first.region.page_id,
                    reading_order=1,
                    region_type="footer",
                    x0=0.0,
                    y0=0.0,
                    x1=1.0,
                    y1=1.0,
                )
            )

        with session_scope() as session:
            parts = _parts_in_document_order(session, graph)
            assert is_consecutive(session, parts[0], parts[1]) is False

    def test_accepts_genuinely_consecutive_parts(self, graph):
        with session_scope() as session:
            parts = _parts_in_document_order(session, graph)
            assert is_consecutive(session, parts[0], parts[1]) is True


def _remove_page_part(session, document_id, page_number):
    """Deletes a page's table part and its cells, as a reprocess of that page does"""
    part = next(part for part in _parts_in_document_order(session, document_id) if part.region.page.page_number == page_number)
    session.query(TableCell).filter(TableCell.region_table_id == part.id).delete(synchronize_session=False)
    session.delete(part)
    session.flush()


class TestSplitAtGaps:
    def test_splits_around_a_removed_middle_page(self, graph):
        # Merges all consecutive tables first
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        # Removes a middle page to simulate a gap in the table parts
        with session_scope() as session:
            merged = _tables(session, graph)[0]
            merged_id = merged.id
            _remove_page_part(session, graph, 2)
            result = split_at_gaps(session, merged)
            assert len(result) == 2
            assert result[0].id == merged_id

        # Verifies the state of the two halves after the split
        with session_scope() as session:
            first = session.get(Table, merged_id)
            second = next(table for table in _tables(session, graph) if table.id != merged_id)

            assert first.title == TITLE
            assert first.is_stitched is False
            assert first.row_count == PART_ROWS[0]
            assert [(p.row_start, p.row_end) for p in ordered_parts(session, first)] == [(0, PART_ROWS[0] - 1)]

            assert second.title is None
            assert second.is_stitched is True
            assert second.col_count == COL_COUNT
            assert second.row_count == PART_ROWS[2] + PART_ROWS[3]
            parts = ordered_parts(session, second)
            assert [part.region.page.page_number for part in parts] == [3, 4]
            assert [part.reading_order for part in parts] == [0, 1]
            assert [(p.row_start, p.row_end) for p in parts] == [
                (0, PART_ROWS[2] - 1),
                (PART_ROWS[2], PART_ROWS[2] + PART_ROWS[3] - 1),
            ]

    def test_removed_first_page_repacks_rows_from_zero(self, graph):
        # Merges all consecutive tables first
        with session_scope() as session:
            merge_consecutive_tables(session, graph)

        # Removes the first page to simulate a gap at the beginning of the table parts
        with session_scope() as session:
            merged = _tables(session, graph)[0]
            _remove_page_part(session, graph, 1)
            result = split_at_gaps(session, merged)
            assert [table.id for table in result] == [merged.id]

        # Verifies that the table's row count and row ranges have been repacked correctly after removing the first page
        with session_scope() as session:
            table = _tables(session, graph)[0]
            assert table.row_count == sum(PART_ROWS[1:])
            claimed: list[int] = []
            for part in ordered_parts(session, table):
                claimed.extend(range(part.row_start, part.row_end + 1))
            assert sorted(claimed) == list(range(table.row_count))

    def test_deletes_a_table_with_no_parts_left(self, graph):
        with session_scope() as session:
            table = next(t for t in _tables(session, graph) if t.title == TITLE)
            table_id = table.id
            _remove_page_part(session, graph, 1)
            assert split_at_gaps(session, table) == []

        with session_scope() as session:
            assert session.get(Table, table_id) is None
