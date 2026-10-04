import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from yarrow_db.models import Document, Job, Page, Region, RegionTable, Table, Warning
from yarrow_db.models.region import RegionImage, RegionText
from yarrow_db.models.table import TableCell
from yarrow_db.session import session_scope
from yarrow_db.utils.table_utils import (
    is_consecutive,
    merge_two_tables,
    split_at_gaps,
)
from yarrow_storage import ObjectNotFoundError, get_storage

from app.celery_app import celery_app

# from app.pipeline.document_parser import DocumentParser
from app.pipeline.document_parser import DocumentParser

# from app.pipeline.document_parser import DocumentParser

logger = logging.getLogger(__name__)

# How many page numbers to name before truncating
MAX_REPORTED_FAILED_PAGES = 10


class AllPagesFailedError(Exception):
    """Every page this run attempted failed inference.

    Raised after the job, document and page rows are committed, so that Celery
    records the task as FAILURE without the generic handler replacing the
    per-page detail already stored. On a partial reprocess this means the job
    failed while the document as a whole may still be usable.
    """


class JobDeletedError(Exception):
    """The job or its document was deleted while the task ran.

    Account deletion removes documents without waiting for their jobs, so this
    is expected rather than a fault.
    """


def _describe_failed_pages(failed: list[int], total: int) -> str:
    shown = ", ".join(str(number) for number in failed[:MAX_REPORTED_FAILED_PAGES])
    if len(failed) > MAX_REPORTED_FAILED_PAGES:
        shown += f", ... (+{len(failed) - MAX_REPORTED_FAILED_PAGES} more)"

    return f"{len(failed)} of {total} page(s) failed: {shown}"


def _document_state(page_states: list[tuple[int, str | None]], total_pages: int) -> tuple[str, str | None]:
    """The document's status and error summary, derived from its page rows"""
    failed = sorted(number for number, status in page_states if status == "failed")
    any_completed = any(status == "completed" for _, status in page_states)

    return (
        "completed" if any_completed else "failed",
        _describe_failed_pages(failed, total_pages) if failed else None,
    )


def _job_deleted(job_id: str) -> bool:
    """Whether the job row is gone, i.e. the error was caused by its deletion"""
    try:
        with session_scope() as session:
            return session.get(Job, UUID(job_id)) is None
    except Exception:
        # Cannot tell, e.g. the database itself is down; report the original error.
        return False


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
def process_document_task(self, job_id: str, page_to_process: tuple | None = None, merge_consecutive_tables: bool = False):
    """
    Processes a document for the given job ID.

    Args:
        self: The Celery task instance.
        job_id: The ID of the job to process.
        page_to_process: Optional tuple specifying which pages to process. This expects a tuple of 1-based page numbers. Defaults to processing every page.
        merge_consecutive_tables: Whether to merge consecutive tables across pages.
    """

    logger.info(f"Starting processing for job {job_id}")
    storage_key = ""
    try:
        with session_scope() as session:
            # Locked so that this and a cancel (US-42) cannot both win: the
            # backend locks the same row before marking a queued job canceled.
            job = session.get(Job, UUID(job_id), with_for_update=True)
            if job is None:
                logger.error(f"Job {job_id} not found; nothing to process")
                return
            if job.status == "canceled":
                # Revoking the Celery task is best effort (a restarted worker
                # forgets revocations), so the row is the source of truth.
                logger.info(f"Job {job_id} was canceled; skipping")
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
            if job is None:
                raise JobDeletedError(job_id)
            job.current_stage = "parsing"
            job.total_pages = len(parser.pages)

        parser.process_sync(page_to_process)

        failed_pages = parser.failed_page_numbers
        total_pages = len(parser.pages)
        attempted = len(page_to_process) if page_to_process is not None else total_pages
        succeeded = attempted - len(failed_pages)
        summary = _describe_failed_pages(failed_pages, attempted) if failed_pages else None

        # Attempting nothing is a no-op, not a failure
        run_failed = bool(attempted) and not succeeded

        with session_scope() as session:
            document = session.get(Document, document_id, with_for_update=True)
            if document is None:
                raise JobDeletedError(job_id)

            page_ids = select(Page.id).where(
                Page.document_id == document_id, (Page.page_number.in_(page_to_process) if page_to_process is not None else True)
            )

            region_ids = select(Region.id).where(Region.page_id.in_(page_ids))

            tables = (
                session.execute(select(Table).join(RegionTable).where(RegionTable.region_id.in_(region_ids), RegionTable.table_id == Table.id))
                .scalars()
                .all()
            )

            # Region tables that are themselves on the targeted pages
            region_table_ids = select(RegionTable.id).where(RegionTable.region_id.in_(region_ids))

            # Delete all objects related to the targeted pages
            statements = [
                delete(TableCell).where(TableCell.region_table_id.in_(region_table_ids)),
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

            # A table that lost a middle page is split into the halves either side of it, and emptied tables are deleted
            for table in tables:
                split_at_gaps(session, table)

            all_objects = parser.to_model_objects(document, target_pages=page_to_process, merge_consecutive_tables=merge_consecutive_tables)
            session.add_all(all_objects)
            session.flush()

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

                    prev_consecutive = prev_region_table is not None and is_consecutive(session, prev_region_table, obj)
                    next_consecutive = next_region_table is not None and is_consecutive(session, obj, next_region_table)

                    if prev_consecutive:
                        merge_two_tables(session, prev_region_table.table, current_table)
                        current_table = prev_region_table.table

                    if next_consecutive:
                        merge_two_tables(session, current_table, next_region_table.table)

            page_states = session.execute(select(Page.page_number, Page.status).where(Page.document_id == document_id)).all()

            job = session.get(Job, UUID(job_id))
            if job is None:
                raise JobDeletedError(job_id)
            
            job.pages_processed = sum(status == "completed" for _, status in page_states)
            job.error_message = summary
            job.current_stage = "parsing" if run_failed else "finished"
            job.status = "failed" if run_failed else "completed"

            document.status, document.error_message = _document_state(page_states, document.page_count or total_pages)

        if run_failed:
            raise AllPagesFailedError(summary or "no pages were produced")

        if failed_pages:
            logger.warning(f"Processed job {job_id} with errors: {summary}")
        else:
            logger.info(f"Successfully processed job {job_id}: {succeeded} page(s)")

    except JobDeletedError:
        logger.info(f"Job {job_id} was deleted while processing; discarding its results")
    except AllPagesFailedError:
        logger.error(f"All pages failed for job {job_id}")
        raise
    except ObjectNotFoundError:
        if _job_deleted(job_id):
            # Account deletion removes the file along with the job.
            logger.info(f"Job {job_id} was deleted before its file was downloaded")
            return
        # Permanent: a missing object never becomes present, so this is not
        # retried -- doing so would only delay the status the user is waiting
        # on. Not re-raised for the same reason.
        logger.error(f"Object missing for job {job_id}: {storage_key}")
        _mark_failed(job_id, f"Uploaded file not found in storage: {storage_key}")
    except Exception as e:
        if _job_deleted(job_id):
            logger.info(f"Job {job_id} was deleted while processing; discarding its results")
            return
        logger.exception(f"Error processing job {job_id}")
        _mark_failed(job_id, str(e))
        # Re-raised so Celery records the task as FAILURE. Swallowing it marks
        # a dead job SUCCESS, which breaks retry-failed-job and every view
        # built on the result backend.
        raise
