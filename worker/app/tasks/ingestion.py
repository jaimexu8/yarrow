import logging
from uuid import UUID

from yarrow_db.models import Document, Job
from yarrow_db.session import session_scope
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


def _describe_failed_pages(failed: list[int], total: int) -> str:
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
def process_document_task(self, job_id: str, merge_consecutive_tables: bool = False):
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

        parser.process_sync()

        failed_pages = parser.failed_page_numbers
        total_pages = len(parser.pages)
        succeeded = total_pages - len(failed_pages)
        summary = (
            _describe_failed_pages(failed_pages, total_pages) if failed_pages else None
        )

        with session_scope() as session:
            document = session.get(Document, document_id)
            all_objects = parser.to_model_objects(
                document, merge_consecutive_tables=merge_consecutive_tables
            )
            session.add_all(all_objects)

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
