"""Unit tests for finalize_document."""

import uuid

from sqlalchemy import update
from yarrow_db.models import Job, JobPage, Page, Region, Table
from yarrow_db.models.table import RegionTable

from app.tasks.ingestion import finalize_document


def _add_text_region(session, document_id, page, reading_order):
    session.add(
        Region(
            id=uuid.uuid4(),
            page_id=page.id,
            page_number=page.page_number,
            reading_order=reading_order,
            region_type="paragraph",
        )
    )


def _add_table_region(
    session, document_id, page, reading_order, col_count=2, row_count=2
):
    """A standalone table part (one Table, one RegionTable) on the page."""
    region = Region(
        id=uuid.uuid4(),
        page_id=page.id,
        page_number=page.page_number,
        reading_order=reading_order,
        region_type="table",
    )
    table = Table(
        id=uuid.uuid4(),
        document_id=document_id,
        row_count=row_count,
        col_count=col_count,
        is_stitched=False,
    )
    region_table = RegionTable(
        id=uuid.uuid4(),
        region_id=region.id,
        table_id=table.id,
        reading_order=0,
        row_start=0,
        row_end=row_count - 1,
        col_start=0,
        col_end=col_count - 1,
    )
    session.add_all([region, table, region_table])
    return region, table


def _seed_document_with_pages(sessions, document, page_count, status="completed"):
    with sessions() as session:
        for n in range(1, page_count + 1):
            session.add(
                Page(
                    document_id=document.id,
                    page_number=n,
                    status=status,
                    width=100.0,
                    height=100.0,
                )
            )


def _seed_terminal_job(sessions, job, page_count, all_completed=True):
    with sessions() as session:
        job = session.get(Job, job.id)
        job.status = "processing"
        job.current_stage = "parsing"
        job.total_pages = page_count
        for n in range(1, page_count + 1):
            session.add(
                JobPage(
                    job_id=job.id,
                    page_number=n,
                    status="completed" if all_completed else "failed",
                )
            )


class TestFinalize:
    def test_all_completed_finishes_job(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)

        finalize_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "completed"
            assert job.current_stage == "finished"
            assert job.error_message is None
            assert job.error_stage is None
            assert job.pages_processed == 3
            assert session.get(Job, job.id).document.status == "completed"
        # Rasters deleted; pages/* never touched.
        for n in (1, 2, 3):
            assert f"documents/{document.id}/rasters/{n}.png" not in storage.store

    def test_all_failed_marks_job_failed(self, sessions, storage, seed):
        document, job = seed(page_count=2, status="processing")
        _seed_document_with_pages(sessions, document, 2, status="failed")
        _seed_terminal_job(sessions, job, 2, all_completed=False)

        finalize_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "failed"
            assert job.current_stage == "failed"
            assert "2 of 2" in job.error_message
            assert job.error_stage == "parsing"
            assert job.document.status == "failed"

    def test_all_failed_with_queue_timeout_stages_queue_timeout(
        self, sessions, storage, seed
    ):
        document, job = seed(page_count=2, status="processing")
        _seed_document_with_pages(sessions, document, 2, status="failed")
        _seed_terminal_job(sessions, job, 2, all_completed=False)
        with sessions() as session:
            session.query(JobPage).filter(
                JobPage.job_id == job.id, JobPage.page_number == 1
            ).update({"error_code": "queue_timeout"})

        finalize_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "failed"
            assert "2 of 2" in job.error_message
            assert job.error_stage == "queue_timeout"

    def test_partial_failure_completes_with_summary(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)
        with sessions() as session:
            session.query(JobPage).filter(
                JobPage.job_id == job.id, JobPage.page_number == 2
            ).update({"status": "failed"})
            session.query(Page).filter(
                Page.document_id == document.id, Page.page_number == 2
            ).update({"status": "failed"})

        finalize_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "completed"
            assert "1 of 3" in job.error_message
            assert job.error_stage is None

    def test_not_all_terminal_is_a_noop(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)
        with sessions() as session:
            session.query(JobPage).filter(
                JobPage.job_id == job.id, JobPage.page_number == 3
            ).update({"status": "queued"})

        finalize_document(str(job.id))

        with sessions() as session:
            # Still parsing; finalize did not run.
            assert session.get(Job, job.id).current_stage == "parsing"

    def test_double_run_is_a_noop(self, sessions, storage, seed):
        document, job = seed(page_count=2, status="processing")
        _seed_document_with_pages(sessions, document, 2)
        _seed_terminal_job(sessions, job, 2, all_completed=True)

        finalize_document(str(job.id))
        with sessions() as session:
            first_processed_at = session.get(Job, job.id).document.processed_at
        # Re-run: the guard (current_stage == finished) returns early, so the
        # document's processed_at is not touched again.
        finalize_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.current_stage == "finished"
            assert job.document.processed_at == first_processed_at

    def test_stitches_3_page_spanning_table(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)
        with sessions() as session:
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(merge_consecutive_tables=True)
            )
            page1 = session.query(Page).filter(Page.page_number == 1).one()
            page2 = session.query(Page).filter(Page.page_number == 2).one()
            page3 = session.query(Page).filter(Page.page_number == 3).one()
            # Page 1: text then table (table at the end).
            _add_text_region(session, document.id, page1, 0)
            _add_table_region(session, document.id, page1, 1)
            # Page 2: only the table (so it is both first and last).
            _add_table_region(session, document.id, page2, 0)
            # Page 3: table then text (table at the start).
            _add_table_region(session, document.id, page3, 0)
            _add_text_region(session, document.id, page3, 1)

        finalize_document(str(job.id))

        with sessions() as session:
            tables = session.query(Table).filter(Table.document_id == document.id).all()
            assert len(tables) == 1
            assert tables[0].is_stitched is True
            parts = (
                session.query(RegionTable)
                .filter(RegionTable.table_id == tables[0].id)
                .all()
            )
            assert len(parts) == 3

    def test_no_stitch_when_flag_false(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)
        with sessions() as session:
            page1 = session.query(Page).filter(Page.page_number == 1).one()
            page2 = session.query(Page).filter(Page.page_number == 2).one()
            page3 = session.query(Page).filter(Page.page_number == 3).one()
            _add_text_region(session, document.id, page1, 0)
            _add_table_region(session, document.id, page1, 1)
            _add_table_region(session, document.id, page2, 0)
            _add_table_region(session, document.id, page3, 0)
            _add_text_region(session, document.id, page3, 1)

        finalize_document(str(job.id))

        with sessions() as session:
            tables = session.query(Table).filter(Table.document_id == document.id).all()
            # No merging: the three standalone parts remain.
            assert len(tables) == 3
            assert all(t.is_stitched is False for t in tables)

    def test_split_at_gaps_repairs_missing_middle_page(self, sessions, storage, seed):
        document, job = seed(page_count=3, status="processing")
        _seed_document_with_pages(sessions, document, 3)
        _seed_terminal_job(sessions, job, 3, all_completed=True)
        with sessions() as session:
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(merge_consecutive_tables=True)
            )
            page1 = session.query(Page).filter(Page.page_number == 1).one()
            page3 = session.query(Page).filter(Page.page_number == 3).one()
            # A table that spanned pages 1,2,3 but page 2's part is gone:
            # create ONE stitched table with parts on page 1 and page 3 only.
            region1 = Region(
                id=uuid.uuid4(),
                page_id=page1.id,
                page_number=1,
                reading_order=0,
                region_type="table",
            )
            region3 = Region(
                id=uuid.uuid4(),
                page_id=page3.id,
                page_number=3,
                reading_order=0,
                region_type="table",
            )
            table = Table(
                id=uuid.uuid4(),
                document_id=document.id,
                row_count=4,
                col_count=2,
                is_stitched=True,
            )
            session.add_all(
                [
                    region1,
                    region3,
                    table,
                    RegionTable(
                        id=uuid.uuid4(),
                        region_id=region1.id,
                        table_id=table.id,
                        reading_order=0,
                        row_start=0,
                        row_end=1,
                        col_start=0,
                        col_end=1,
                    ),
                    RegionTable(
                        id=uuid.uuid4(),
                        region_id=region3.id,
                        table_id=table.id,
                        reading_order=1,
                        row_start=2,
                        row_end=3,
                        col_start=0,
                        col_end=1,
                    ),
                ]
            )

        finalize_document(str(job.id))

        with sessions() as session:
            tables = session.query(Table).filter(Table.document_id == document.id).all()
            # The gap (page 2 missing) splits the stitched table into two.
            assert len(tables) == 2
            assert all(t.is_stitched is False for t in tables)
