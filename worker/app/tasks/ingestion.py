import io
import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm.exc import ObjectDeletedError
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


class JobCanceledError(Exception):
    """The user canceled the job while it was processing (US-42).

    The backend has already marked the job and document canceled; the run
    stops and nothing it produced is saved.
    """


def _job_canceled(job_id: str) -> bool:
    """Whether the user has canceled this job"""
    try:
        with session_scope() as session:
            job = session.get(Job, UUID(job_id))
            return job is not None and job.status == "canceled"
    except Exception:
        return False


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


def _delete_stored(keys: list[str]) -> None:
    """Best effort: an object left behind only wastes space"""
    for key in keys:
        try:
            get_storage().delete_file(key)
        except Exception:
            logger.exception(f"Could not delete stored object {key}")


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
        # Locked (document, then job, like everywhere else) before checking,
        # so a cancel cannot land between the check and this write.
        if session.get(Document, job.document_id, with_for_update=True) is None:
            return  # Deleted meanwhile; nothing left to mark.
        try:
            session.refresh(job, with_for_update=True)
        except ObjectDeletedError:
            return
        if job.status == "canceled":
            # The user canceled it (US-42); that is the state they should see.
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

    # Figure crops uploaded for this run, removed again if its results are not
    # committed. Replaced crops are removed only once the new ones are.
    uploaded_image_keys: list[str] = []
    replaced_image_keys: list[str] = []
    committed = False
    try:
        with session_scope() as session:
            # Locked so that this and a cancel (US-42) cannot both win. Document
            # row first, then job: the same order as the cancel endpoint,
            # document deletion and the results commit below, so they cannot
            # deadlock.
            job = session.get(Job, UUID(job_id))
            if job is None:
                logger.error(f"Job {job_id} not found; nothing to process")
                return
            session.get(Document, job.document_id, with_for_update=True)
            session.refresh(job, with_for_update=True)
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
            if job.status == "canceled":
                # Canceled while downloading: skip the slow OCR step.
                raise JobCanceledError(job_id)
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

            # Checked while holding the document lock, which the cancel
            # endpoint takes before marking a job canceled: either the cancel
            # landed first and these results are dropped, or this commit lands
            # first and the cancel is refused (US-42).
            job = session.get(Job, UUID(job_id), with_for_update=True)
            if job is None:
                raise JobDeletedError(job_id)
            if job.status == "canceled":
                raise JobCanceledError(job_id)

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

            replaced_image_keys = list(
                session.execute(
                    select(RegionImage.image_key).where(
                        RegionImage.region_id.in_(region_ids),
                        RegionImage.image_key.is_not(None),
                    )
                )
                .scalars()
                .all()
            )

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

            # A figure whose crop cannot be stored keeps its region, just not its picture
            failed_image_keys: set[str] = set()
            for image_key, image in parser.figure_images.items():
                try:
                    get_storage().upload_file(io.BytesIO(image), image_key)
                    uploaded_image_keys.append(image_key)
                except Exception:
                    logger.exception(f"Could not store figure {image_key}")
                    failed_image_keys.add(image_key)
            all_objects = [
                obj for obj in all_objects if not (isinstance(obj, RegionImage) and obj.image_key in failed_image_keys)
            ]

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

        committed = True
        _delete_stored(replaced_image_keys)

        if run_failed:
            raise AllPagesFailedError(summary or "no pages were produced")

        if failed_pages:
            logger.warning(f"Processed job {job_id} with errors: {summary}")
        else:
            logger.info(f"Successfully processed job {job_id}: {succeeded} page(s)")

    except JobCanceledError:
        logger.info(f"Job {job_id} was canceled while processing; discarding its results")
        _delete_stored(uploaded_image_keys)
    except JobDeletedError:
        logger.info(f"Job {job_id} was deleted while processing; discarding its results")
        _delete_stored(uploaded_image_keys)
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
        if not committed:
            _delete_stored(uploaded_image_keys)
        if _job_deleted(job_id):
            logger.info(f"Job {job_id} was deleted while processing; discarding its results")
            return
        if _job_canceled(job_id):
            # An error in a run the user already canceled is not a failure.
            logger.info(f"Job {job_id} was canceled while processing ({e}); discarding")
            return
        logger.exception(f"Error processing job {job_id}")
        _mark_failed(job_id, str(e))
        # Re-raised so Celery records the task as FAILURE. Swallowing it marks
        # a dead job SUCCESS, which breaks retry-failed-job and every view
        # built on the result backend.
        raise
