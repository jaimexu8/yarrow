import logging
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import delete, select, func
from yarrow_db.models import Page, Region, Table, RegionTable, Document, Job, Warning
from yarrow_db.models.region import RegionImage, RegionText
from yarrow_db.models.table import TableCell
from yarrow_db.session import session_scope
from yarrow_db.utils.table_utils import is_consecutive, merge_two_tables
from yarrow_storage import ObjectNotFoundError, get_storage

from app.celery_app import celery_app

# from app.pipeline.document_parser import DocumentParser
from app.pipeline.document_parser import DocumentParser

logger = logging.getLogger(__name__)

# How many page numbers to name before truncating. A 400-page scan processed
# while the cluster was down would otherwise put 400 numbers in a message the
# UI renders inline.
MAX_REPORTED_FAILED_PAGES = 10


class AllPagesFailedError(Exception):
    """Every page failed inference.

    Raised after the job, document and page rows are committed, so that Celery
    records the task as FAILURE without the generic handler replacing the
    per-page detail already stored.
    """


def _describe_failed_pages(failed: List[int], total: int) -> str:
    shown = ", ".join(str(number) for number in failed[:MAX_REPORTED_FAILED_PAGES])
    if len(failed) > MAX_REPORTED_FAILED_PAGES:
        shown += f", ... (+{len(failed) - MAX_REPORTED_FAILED_PAGES} more)"

    return f"{len(failed)} of {total} page(s) failed: {shown}"


def _mark_failed(job_id: str, message: str) -> None:
    with session_scope() as session:
        job = session.get(Job, UUID(job_id))
        if job is None:
            logger.error(f"Job {job_id} vanished while recording failure")
            return
        job.status = "failed"
        job.error_message = message
        job.document.status = "failed"
        job.document.error_message = message


@celery_app.task(bind=True)
def process_document_task(self, job_id: str, page_to_process: Optional[Tuple] = None, merge_consecutive_tables: bool = False):
    """
    Processes a document for the given job ID.

    Args:
        self: The Celery task instance.
        job_id: The ID of the job to process.
        page_to_process: Optional tuple specifying which pages to process. Defaults to processing every page.
        merge_consecutive_tables: Whether to merge consecutive tables across pages.
    """

    logger.info(f"Starting processing for job {job_id}")
    storage_key = ""
    try:
        with session_scope() as session:
            job = session.get(Job, UUID(job_id))
            if job is None:
                logger.error(f"Job {job_id} not found; nothing to process")
                return
            job.status = "processing"
            job.current_stage = "downloading"
            job.document.status = "processing"

            # Read out as plain values while the instances are still attached.
            document_id = job.document.id
            storage_key = job.document.storage_key

        file_data = get_storage().download_bytes(storage_key)
        logger.info(f"Downloaded {len(file_data)} bytes from {storage_key}")

        parser = DocumentParser()
        parser.load_file(file_data)

        with session_scope() as session:
            job = session.get(Job, UUID(job_id))
            job.current_stage = "parsing"
            job.total_pages = len(parser.pages)

        parser.process_sync(page_to_process)

        failed_pages = parser.failed_page_numbers
        total_pages = len(parser.pages)
        succeeded = total_pages - len(failed_pages)
        summary = _describe_failed_pages(failed_pages, total_pages) if failed_pages else None

        with session_scope() as session:
            document = session.get(Document, document_id)

            page_ids = select(Page.id).where(
                Page.document_id == document_id, (Page.page_number.in_(page_to_process) if page_to_process is not None else True)
            )

            region_ids = select(Region.id).where(Region.page_id.in_(page_ids))

            tables = (
                session.execute(select(Table).join(RegionTable).where(RegionTable.region_id.in_(region_ids), RegionTable.table_id == Table.id))
                .scalars()
                .all()
            )

            region_tables = (
                session.execute(
                    select(RegionTable).where((RegionTable.region_id.in_(region_ids)) | (RegionTable.table_id.in_([t.id for t in tables])))
                )
                .scalars()
                .all()
            )

            # Delete all objects related to the targeted pages
            statements = [
                delete(TableCell).where(TableCell.region_table_id.in_([rt.id for rt in region_tables])),
                delete(RegionTable).where(RegionTable.region_id.in_(region_ids)),
                delete(RegionText).where(RegionText.region_id.in_(region_ids)),
                delete(RegionImage).where(RegionImage.region_id.in_(region_ids)),
                delete(Warning).where(Warning.page_id.in_(page_ids)),
                delete(Region).where(Region.id.in_(region_ids)),
                delete(Page).where(Page.id.in_(page_ids)),
            ]

            for statement in statements:
                session.execute(statement)

            session.flush()

            # Update table objects
            for table in tables:
                remaining_region_table = session.execute(select(RegionTable.id).where(RegionTable.table_id == table.id).limit(1)).scalar_one_or_none()

                # Checks if deleting all the region tables in the target pages results in tables being orphaned
                if not remaining_region_table:
                    session.delete(table)
                else:
                    remaining_count = session.execute(select(func.count(RegionTable.id)).where(RegionTable.table_id == table.id)).scalar_one()

                    if remaining_count == 1:
                        table.stitched = False

                    # Updates the row count of the table to reflect the remaining region tables
                    table.row_count = session.execute(select(func.sum(RegionTable.row_count)).where(RegionTable.table_id == table.id)).scalar_one()

            all_objects = parser.to_model_objects(document, target_pages=page_to_process, merge_consecutive_tables=merge_consecutive_tables)
            session.add_all(all_objects)

            if merge_consecutive_tables:
                for obj in all_objects:
                    if not isinstance(obj, RegionTable):
                        continue

                    page_number = obj.region.page.page_number
                    prev_page = (
                        session.query(Page)
                        .filter(
                            Page.document_id == document_id,
                            Page.page_number == page_number - 1,
                        )
                        .first()
                    )
                    next_page = (
                        session.query(Page)
                        .filter(
                            Page.document_id == document_id,
                            Page.page_number == page_number + 1,
                        )
                        .first()
                    )

                    # Gets the region table in the previous page that has the highest reading order
                    prev_region_table = None
                    if prev_page is not None:
                        prev_max_reading_order = session.query(func.max(Region.reading_order)).filter(Region.page_id == prev_page.id).scalar()
                        if prev_max_reading_order is not None:
                            prev_region_table = (
                                session.query(RegionTable)
                                .join(Region)
                                .filter(Region.page_id == prev_page.id, Region.reading_order == prev_max_reading_order)
                                .first()
                            )

                    # Gets the region table in the next page that has the lowest reading order
                    next_region_table = None
                    if next_page is not None:
                        next_min_reading_order = session.query(func.min(Region.reading_order)).filter(Region.page_id == next_page.id).scalar()
                        if next_min_reading_order is not None:
                            next_region_table = (
                                session.query(RegionTable)
                                .join(Region)
                                .filter(Region.page_id == next_page.id, Region.reading_order == next_min_reading_order)
                                .first()
                            )

                    current_table = obj.table

                    # Merge the current table with the previous table if they are consecutive
                    if prev_region_table is not None and is_consecutive(session, prev_region_table, obj):
                        merge_two_tables(session, prev_region_table.table, current_table)
                        current_table = prev_region_table.table

                    # Merge the current table with the next table if they are consecutive
                    if next_region_table is not None and is_consecutive(session, obj, next_region_table):
                        merge_two_tables(session, current_table, next_region_table.table)

                    # TODO: Merge with hollowed table

            job = session.get(Job, UUID(job_id))
            job.pages_processed = succeeded
            job.error_message = summary
            job.current_stage = "finished" if succeeded else "parsing"
            job.status = "completed" if succeeded else "failed"
            document.status = "completed" if succeeded else "failed"
            document.error_message = summary

        if not succeeded:
            raise AllPagesFailedError(summary or "no pages were produced")

        if failed_pages:
            logger.warning(f"Processed job {job_id} with errors: {summary}")
        else:
            logger.info(f"Successfully processed job {job_id}: {succeeded} page(s)")

    except AllPagesFailedError:
        logger.error(f"All pages failed for job {job_id}")
        raise
    except ObjectNotFoundError:
        # Permanent: a missing object never becomes present, so this is not
        # retried -- doing so would only delay the status the user is waiting
        # on. Not re-raised for the same reason.
        logger.error(f"Object missing for job {job_id}: {storage_key}")
        _mark_failed(job_id, f"Uploaded file not found in storage: {storage_key}")
    except Exception as e:
        logger.exception(f"Error processing job {job_id}")
        _mark_failed(job_id, str(e))
        # Re-raised so Celery records the task as FAILURE. Swallowing it marks
        # a dead job SUCCESS, which breaks retry-failed-job and every view
        # built on the result backend.
        raise
