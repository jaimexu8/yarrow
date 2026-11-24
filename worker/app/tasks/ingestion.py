import base64
import io
import json
import logging
import random
from datetime import UTC, datetime
from uuid import UUID

import filetype
import redis
import requests
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError
from yarrow_db.models import (
    OPEN_PAGE_STATUSES,
    Document,
    Job,
    JobPage,
    JobStage,
    Page,
    Region,
    RegionTable,
    Table,
    Warning,
)
from yarrow_db.models.region import RegionImage, RegionText
from yarrow_db.models.table import TableCell
from yarrow_db.session import session_scope
from yarrow_db.utils.table_utils import (
    is_consecutive,
    merge_two_tables,
    split_at_gaps,
)
from yarrow_storage import ObjectNotFoundError, get_storage, thumbnail_key

from app.celery_app import celery_app
from app.core.config import PIPELINE_VERSION, settings
from app.pipeline.document_parser import (
    DocumentParser,
    ModelInfo,
    PageEvent,
    page_error_code,
)
from app.pipeline.text_layer import (
    Word,
    deserialize_words,
    evaluate_text_layer,
    extract_page_text,
    scale_words,
    serialize_words,
)
from app.pipeline.thumbnails import build_thumbnail

logger = logging.getLogger(__name__)

# How many page numbers to name before truncating
MAX_REPORTED_FAILED_PAGES = 10


class JobCanceledError(Exception):
    """The user canceled the job while it was processing (US-42).

    The backend has already marked the job and document canceled; the run
    stops and nothing it produced is saved.
    """


class JobDeletedError(Exception):
    """The job or its document was deleted while the task ran.

    Account deletion removes documents without waiting for their jobs, so this
    is expected rather than a fault.
    """


def _job_canceled(job_id: str) -> bool:
    """Whether the user has canceled this job"""
    try:
        with session_scope() as session:
            job = session.get(Job, UUID(job_id))
            return job is not None and job.status == "canceled"
    except Exception:
        return False


def _describe_failed_pages(failed: list[int], total: int) -> str:
    shown = ", ".join(str(number) for number in failed[:MAX_REPORTED_FAILED_PAGES])
    if len(failed) > MAX_REPORTED_FAILED_PAGES:
        shown += f", ... (+{len(failed) - MAX_REPORTED_FAILED_PAGES} more)"

    return f"{len(failed)} of {total} page(s) failed: {shown}"


def _document_state(
    page_states: list[tuple[int, str | None]], total_pages: int
) -> tuple[str, str | None]:
    """The document's status and error summary, derived from its page rows"""
    failed = sorted(number for number, status in page_states if status == "failed")
    any_completed = any(status == "completed" for _, status in page_states)

    return (
        "completed" if any_completed else "failed",
        _describe_failed_pages(failed, total_pages) if failed else None,
    )


def _run_model(page_models: dict[int, ModelInfo]) -> ModelInfo | None:
    counts: dict[ModelInfo, int] = {}
    latest: dict[ModelInfo, int] = {}
    for order, model in enumerate(page_models.values()):
        counts[model] = counts.get(model, 0) + 1
        latest[model] = order
    return max(counts, key=lambda model: (counts[model], latest[model]), default=None)


def _delete_stored(keys: list[str]) -> None:
    """Best effort: an object left behind only wastes space"""
    for key in keys:
        try:
            get_storage().delete_file(key)
        except Exception:
            logger.exception(f"Could not delete stored object {key}")


def _store_thumbnails(
    document_id: UUID, parser: DocumentParser, page_numbers: list[int]
) -> dict[int, str]:
    stored: dict[int, str] = {}
    for number in page_numbers:
        key = thumbnail_key(document_id, number)
        try:
            get_storage().upload_file(
                io.BytesIO(build_thumbnail(parser.pages[number - 1])), key
            )
        except Exception:
            logger.exception(f"Could not store thumbnail {key}")
            continue
        stored[number] = key
    return stored


def _discard_thumbnails(document_id: UUID | None, keys: list[str]) -> None:
    """Remove uploaded thumbnails that no committed Page row still references.

    A reprocess overwrites the same deterministic keys. If its run aborts, the
    earlier run's rows still point at them, so those objects must stay.
    """
    if document_id is None or not keys:
        return
    try:
        with session_scope() as session:
            kept = set(
                session.scalars(
                    select(Page.thumbnail_key).where(
                        Page.document_id == document_id,
                        Page.thumbnail_key.in_(keys),
                    )
                )
            )
    except Exception:
        logger.exception(f"Could not check thumbnails of document {document_id}")
        return
    _delete_stored([key for key in keys if key not in kept])


def _text_layer_key(document_id: UUID, page_number: int) -> str:
    return f"documents/{document_id}/text_layer/{page_number}.json"


def _store_text_layers(
    job_id: str,
    document_id: UUID,
    file_data: bytes,
    page_numbers: list[int],
    skip_text_layer: bool,
) -> None:
    """Stage usable PDF text words for the parse stage.

    A rejection or any failure leaves the page on the ML path. These files are
    an optimization, so a broken extractor or object store must not fail the
    batch.
    """
    if not settings.TEXT_LAYER_ENABLED or skip_text_layer:
        return
    try:
        kind = filetype.guess(file_data)
    except Exception:
        logger.exception(f"Could not detect the file type for job {job_id}")
        return
    if kind is None or kind.extension != "pdf":
        return

    for number in page_numbers:
        try:
            page_text = extract_page_text(file_data, number)
            decision = evaluate_text_layer(page_text)
            if not decision.usable:
                logger.debug(
                    f"Text layer rejected for page {number} of job {job_id}: "
                    f"{decision.reason}"
                )
                continue
            payload = serialize_words(scale_words(page_text))
            key = _text_layer_key(document_id, number)
            get_storage().upload_file(
                io.BytesIO(json.dumps(payload).encode("utf-8")), key
            )
        except Exception:
            logger.exception(
                f"Could not store text layer for page {number} of job {job_id}"
            )


def _load_text_layer_words(
    document_id: UUID, page_no: int, skip_text_layer: bool
) -> list[Word]:
    """Read staged words for one page, or [] when the ML path must be used."""
    if not settings.TEXT_LAYER_ENABLED or skip_text_layer:
        return []
    key = _text_layer_key(document_id, page_no)
    try:
        payload = json.loads(get_storage().download_bytes(key))
        return deserialize_words(payload)
    except ObjectNotFoundError:
        logger.debug(f"No text layer at {key}; using the ML path")
    except Exception:
        logger.exception(f"Could not read text layer {key}; using the ML path")
    return []


def _job_deleted(job_id: str) -> bool:
    """Whether the job row is gone, i.e. the error was caused by its deletion"""
    try:
        with session_scope() as session:
            return session.get(Job, UUID(job_id)) is None
    except Exception:
        # Cannot tell, e.g. the database itself is down; report the original error.
        return False


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _run_page_numbers(page_count: int, page_to_process: tuple | None) -> list[int]:
    return [
        number
        for number in range(1, page_count + 1)
        if page_to_process is None or number in page_to_process
    ]


def _page_chunks(numbers: list[int], size: int):
    """Split ordered page numbers into contiguous batches."""
    size = max(1, size)
    for start in range(0, len(numbers), size):
        yield numbers[start : start + size]


def _lock_running_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, UUID(job_id), with_for_update=True)
    if job is None:
        raise JobDeletedError(job_id)
    if job.status == "canceled":
        raise JobCanceledError(job_id)
    return job


def _set_stage(job_id: str, stage: JobStage) -> None:
    with session_scope() as session:
        _lock_running_job(session, job_id).current_stage = stage


def _record_page_event(
    job_id: UUID, parser: DocumentParser, page_number: int, event: PageEvent
) -> None:
    now = _utcnow()
    this_page = (JobPage.job_id == job_id, JobPage.page_number == page_number)
    with session_scope() as session:
        if event == "started":
            session.execute(
                update(JobPage)
                .where(*this_page, JobPage.status == "queued")
                .values(status="processing", started_at=now)
                .execution_options(synchronize_session=False)
            )
            return

        running = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "processing")
            .values(updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if not running.rowcount:
            return

        outcome = {"status": event, "finished_at": now}
        if event == "failed":
            error = parser.page_errors.get(page_number - 1)
            outcome["error_code"] = error.code if error else "unknown"
            outcome["error_message"] = error.message if error else None
        finished = session.execute(
            update(JobPage)
            .where(*this_page, JobPage.status.in_(OPEN_PAGE_STATUSES))
            .values(**outcome)
            .execution_options(synchronize_session=False)
        )
        if finished.rowcount:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "processing")
                .values(pages_processed=Job.pages_processed + 1, updated_at=now)
                .execution_options(synchronize_session=False)
            )


def _mark_failed(job_id: str, message: str, error_stage: str = "downloading") -> None:
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
        job.current_stage = "failed"
        job.error_stage = error_stage
        job.error_message = message
        job.document.status = "failed"
        job.document.error_message = message


def _page_counters_client():
    """Valkey DB 3 client for per-page delivery counters and sweeper rate-limit
    keys.

    Returns None when the store cannot be reached: the counters are a safety
    net and the database is the source of truth, so a Valkey blip must not fail
    a page or a sweep.
    """
    try:
        return redis.from_url(settings.PAGE_COUNTERS_REDIS_URL, socket_timeout=2)
    except Exception:
        logger.exception("Page counters store is unreachable")
        return None


def _incr_page_delivery(job_id: str, page_no: int) -> int | None:
    """Count a delivery of this page (Valkey DB 3, TTL 2 h on first delivery).

    Returns the count after incrementing, or None when Valkey is unreachable
    (the delivery cap is then skipped).
    """
    client = _page_counters_client()
    if client is None:
        return None
    key = f"page_deliveries:{job_id}:{page_no}"
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, 7200)
        return int(count)
    except Exception:
        logger.exception(f"Could not count a delivery for page {job_id}:{page_no}")
        return None


def _clear_page_delivery(job_id: str, page_no: int) -> None:
    """Drop a page's delivery counter (ingest, so a reprocess does not inherit
    a stale count)."""
    client = _page_counters_client()
    if client is None:
        return
    try:
        client.delete(f"page_deliveries:{job_id}:{page_no}")
    except Exception:
        logger.exception(f"Could not clear the delivery counter for {job_id}:{page_no}")


def _is_swept(job_id: str) -> bool:
    """Whether the sweeper already acted on this job within the rate-limit
    window."""
    client = _page_counters_client()
    if client is None:
        return False
    try:
        return client.get(f"swept:{job_id}") is not None
    except Exception:
        logger.exception(f"Could not check the sweep marker for job {job_id}")
        return False


def _mark_swept(job_id: str) -> None:
    """Rate-limit the sweeper so it does not re-enqueue a job it just acted on."""
    client = _page_counters_client()
    if client is None:
        return
    try:
        client.set(f"swept:{job_id}", "1", ex=1800)
    except Exception:
        logger.exception(f"Could not mark job {job_id} as swept")


def _delete_page_children(
    session: Session, page_ids: list[UUID], *, delete_warnings: bool = True
) -> None:
    """Delete a page's child rows, NOT its Table rows.

    A merged Table spans multiple pages,
    so deleting a page's RegionTable leaves a gap that finalize's
    split_at_gaps repairs; a standalone table is orphaned by the delete and
    dropped there too.

    `delete_warnings` keeps ingest's behaviour (drop every child so a pending
    placeholder shows no leftover content; ingest re-inserts the run's
    auto-rotation warning in the same transaction). parse_page passes False:
    it inserts its own quality warnings from parse_single_page but must not
    delete the auto-rotation warning ingest already recorded.
    """
    if not page_ids:
        return
    region_ids = select(Region.id).where(Region.page_id.in_(page_ids))
    region_table_ids = select(RegionTable.id).where(
        RegionTable.region_id.in_(region_ids)
    )
    session.execute(
        delete(TableCell).where(TableCell.region_table_id.in_(region_table_ids))
    )
    session.execute(delete(RegionTable).where(RegionTable.region_id.in_(region_ids)))
    session.execute(delete(RegionText).where(RegionText.region_id.in_(region_ids)))
    session.execute(delete(RegionImage).where(RegionImage.region_id.in_(region_ids)))
    if delete_warnings:
        session.execute(delete(Warning).where(Warning.page_id.in_(page_ids)))
    session.execute(delete(Region).where(Region.id.in_(region_ids)))


def _recount_progress(session: Session, job_id: str) -> tuple[int, int]:
    """Set jobs.pages_processed to the terminal job_pages count and refresh
    updated_at.

    Raw SQL because a statement-level update does not fire the ORM's onupdate.
    Counts both terminal states. The timestamp is bound
    from Python (not now()) and the id as its hex form (no dashes) so the
    statement matches the id columns on both the SQLite test database (which
    stores UUIDs as 32-hex) and Postgres (which accepts the 32-hex UUID form).
    Returns (pages_processed, total_pages).
    """
    row = session.execute(
        text(
            "UPDATE jobs SET "
            "pages_processed = (SELECT count(*) FROM job_pages "
            "WHERE job_id = :job_id AND status IN ('completed','failed')), "
            "updated_at = :now "
            "WHERE id = :job_id "
            "RETURNING pages_processed, total_pages"
        ),
        {"job_id": UUID(job_id).hex, "now": _utcnow()},
    ).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def _reconcile_job_pages(session: Session, job_id: str) -> None:
    """Copy this job's job_pages back from the final page rows so /progress is
    consistent for a terminal job."""
    job = session.get(Job, UUID(job_id))
    if job is None:
        return
    pages = {
        row.page_number: row
        for row in session.execute(
            select(Page).where(Page.document_id == job.document_id)
        ).scalars()
    }
    now = _utcnow()
    for job_page in session.execute(
        select(JobPage).where(JobPage.job_id == job.id)
    ).scalars():
        page = pages.get(job_page.page_number)
        if page is None:
            continue
        job_page.status = page.status
        job_page.error_code = page.error_code
        job_page.error_message = page.error_message
        job_page.finished_at = job_page.finished_at or now


def _is_retriable(error: BaseException | None) -> bool:
    """Classify the raw gateway exception: the stored error_code cannot
    tell a gateway 500 from a 4xx, so the retry split reads the raw exception."""
    if error is None:
        return False
    if isinstance(error, SoftTimeLimitExceeded):
        return True
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        return getattr(error.response, "status_code", None) in {500, 502, 503, 504}
    return False


def _enqueue_parse_page(job_id: str, page_no: int) -> None:
    celery_app.send_task(
        "app.tasks.ingestion.parse_page", args=[job_id, page_no], queue="ocr"
    )


def _maybe_start_parsing(job_id: str) -> None:
    """Flip a preparing job to parsing once every run page has a queued row.

    Set equality on the actual row page numbers, not a count or a hard-coded
    range, so a partial reprocess still fires with its own set of pages. The
    stage check under the row lock keeps a second caller from firing twice.
    """
    to_parse: list[int] = []
    try:
        with session_scope() as session:
            job = _lock_running_job(session, job_id)
            if job.status != "processing" or job.current_stage != "preparing":
                return
            rows = session.execute(
                select(JobPage.page_number, JobPage.status).where(
                    JobPage.job_id == job.id
                )
            ).all()
            if not rows or len(rows) != job.total_pages:
                return
            if any(status != "queued" for _number, status in rows):
                return
            to_parse = [number for number, _status in rows]
            job.current_stage = "parsing"
    except (JobCanceledError, JobDeletedError):
        return

    for number in to_parse:
        # A reprocess must not inherit a stale delivery count within the
        # counters' TTL.
        _clear_page_delivery(job_id, number)
        try:
            _enqueue_parse_page(job_id, number)
        except Exception:
            logger.exception(f"Could not enqueue parse_page for {job_id}:{number}")


def _enqueue_finalize(job_id: str) -> None:
    celery_app.send_task(
        "app.tasks.ingestion.finalize_document", args=[job_id], queue="default"
    )


def _retry_backoff(retries: int) -> int:
    """Backoff starting at 5 s, capped at 60 s, with jitter."""
    base = min(60.0, 5.0 * (2**retries))
    return int(base * (0.5 + random.random()))


def _bump_progress(job_id: str) -> None:
    """Recount progress; enqueue finalize once every run page is terminal.

    The recount is idempotent, so redelivered deliveries that
    early-exit still land here and never double-count.
    """
    try:
        with session_scope() as session:
            pages_processed, total_pages = _recount_progress(session, job_id)
    except Exception:
        logger.exception(f"Could not recount progress for job {job_id}")
        return
    if total_pages and pages_processed >= total_pages:
        try:
            _enqueue_finalize(job_id)
        except Exception:
            # The broker is unreachable; the sweeper will enqueue finalize.
            logger.exception(f"Could not enqueue finalize for job {job_id}")


def _raster_size(page_b64: str) -> tuple[float | None, float | None]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(page_b64))) as image:
            image.load()
        return float(image.width), float(image.height)
    except Exception:
        logger.exception("Could not read a raster page size")
        return None, None


def _is_rotated(parser: DocumentParser, page_index: int) -> bool:
    """Whether this run left a corrected page image for the page at page_index."""
    if page_index in parser.page_orientations:
        return True
    return (
        page_index in parser.manual_rotations
        and parser.manual_rotations[page_index] % 360 != 0
    )


@celery_app.task(
    name="app.tasks.ingestion.ingest_document",
    queue="default",
    soft_time_limit=600,
    time_limit=660,
    acks_late=False,
)
def ingest_document(
    job_id: str,
    pages: list[int] | None = None,
    merge_consecutive_tables: bool = False,
):
    """Count the run's pages and fan out prepare_pages batches on the ocr queue.

    Idempotent: the sweeper re-enqueues it when a job gets stuck in
    downloading/preparing. A second pass while the job is preparing heals the
    missing batches instead of resetting work already done.
    """
    logger.info(f"Starting ingest for job {job_id}")
    storage_key = ""
    already_preparing = False

    with session_scope() as session:
        job = session.get(Job, UUID(job_id))
        if job is None:
            logger.error(f"Job {job_id} not found; nothing to ingest")
            return
        session.get(Document, job.document_id, with_for_update=True)
        session.refresh(job, with_for_update=True)
        if job.status == "canceled":
            logger.info(f"Job {job_id} was canceled; skipping ingest")
            return
        # A sweeper re-enqueue while batches are being prepared must heal, not
        # reset: keep that stage and the rows prepare_pages already seeded.
        already_preparing = job.current_stage == "preparing" and (
            session.execute(
                select(JobPage.id).where(JobPage.job_id == job.id).limit(1)
            ).first()
            is not None
        )
        job.status = "processing"
        if not already_preparing:
            job.current_stage = "downloading"
        job.document.status = "processing"
        storage_key = job.document.storage_key

    file_data = None
    try:
        file_data = get_storage().download_bytes(storage_key)
    except ObjectNotFoundError:
        if _job_deleted(job_id):
            logger.info(f"Job {job_id} was deleted before its file was downloaded")
            return
        logger.error(f"Object missing for job {job_id}: {storage_key}")
        _mark_failed(job_id, f"Uploaded file not found in storage: {storage_key}")
        raise
    logger.info(f"Downloaded {len(file_data)} bytes from {storage_key}")

    parser = DocumentParser()
    try:
        page_count = parser.count_pages(file_data)
    except ValueError as error:
        _mark_failed(job_id, str(error))
        raise

    run_page_numbers = _run_page_numbers(page_count, pages)

    if already_preparing:
        with session_scope() as session:
            queued_pages = set(
                session.execute(
                    select(JobPage.page_number).where(
                        JobPage.job_id == UUID(job_id), JobPage.status == "queued"
                    )
                ).scalars()
            )
        to_enqueue = [
            number for number in run_page_numbers if number not in queued_pages
        ]
    else:
        with session_scope() as session:
            job = _lock_running_job(session, job_id)
            job.total_pages = len(run_page_numbers)
            job.pages_processed = 0
            job.merge_consecutive_tables = merge_consecutive_tables
            job.document.page_count = page_count
            session.execute(delete(JobPage).where(JobPage.job_id == job.id))
        to_enqueue = run_page_numbers
        _set_stage(job_id, "preparing")

    if not run_page_numbers:
        _set_stage(job_id, "parsing")
        return

    for chunk in _page_chunks(to_enqueue, settings.INGEST_PAGE_BATCH_SIZE):
        try:
            celery_app.send_task(
                "app.tasks.ingestion.prepare_pages",
                args=[job_id, chunk],
                queue="ocr",
            )
        except Exception:
            # The broker is unreachable; the job stays preparing and the
            # sweeper re-enqueues ingest, which only sends missing batches.
            logger.exception(f"Could not enqueue prepare_pages for {job_id}:{chunk}")


@celery_app.task(
    name="app.tasks.ingestion.prepare_pages",
    queue="ocr",
    soft_time_limit=600,
    time_limit=660,
    acks_late=False,
)
def prepare_pages(job_id: str, page_numbers: list[int]):
    """Load, orient, seed and upload one batch of an ingest run.

    Idempotent per batch: a duplicate delivery upserts the same rows. The
    parsing gate opens only once every run page has a queued job_page.
    """
    logger.info(f"Preparing pages {page_numbers} for job {job_id}")
    try:
        with session_scope() as session:
            job = _lock_running_job(session, job_id)
            if job.status != "processing" or job.current_stage != "preparing":
                return
            document = session.get(Document, job.document_id)
            if document is None:
                return
            document_id = document.id
            storage_key = document.storage_key
            skip_text_layer = bool(job.skip_text_layer)
    except (JobCanceledError, JobDeletedError):
        logger.info(f"Job {job_id} was canceled or deleted; skipping prepare_pages")
        return

    file_data = get_storage().download_bytes(storage_key)
    _store_text_layers(job_id, document_id, file_data, page_numbers, skip_text_layer)

    parser = DocumentParser()
    parser.load_pages(file_data, page_numbers)

    # Manual-rotation overrides first, then auto detection for this batch.
    with session_scope() as session:
        rotation_rows = session.execute(
            select(Page.page_number, Page.manual_rotation_deg).where(
                Page.document_id == document_id,
                Page.page_number.in_(page_numbers),
                Page.manual_rotation_deg.is_not(None),
            )
        ).all()
    parser.apply_manual_rotations(
        {
            page_number - 1: degrees
            for page_number, degrees in rotation_rows
            if page_number is not None
            and degrees is not None
            and 1 <= page_number <= len(parser.pages)
        }
    )
    parser.correct_orientations(tuple(page_numbers))

    thumbnail_keys = _store_thumbnails(document_id, parser, page_numbers)
    thumbnails_committed = False
    try:
        # Upload this batch's corrected rasters; a rotated page also gets its
        # upright image at pages/{n}.png because parse_page will not re-rotate.
        for number in page_numbers:
            raster = base64.b64decode(parser.pages[number - 1])
            get_storage().upload_file(
                io.BytesIO(raster), f"documents/{document_id}/rasters/{number}.png"
            )
            if _is_rotated(parser, number - 1):
                get_storage().upload_file(
                    io.BytesIO(raster), f"documents/{document_id}/pages/{number}.png"
                )

        with session_scope() as session:
            job = _lock_running_job(session, job_id)
            if job.status != "processing" or job.current_stage != "preparing":
                _discard_thumbnails(document_id, list(thumbnail_keys.values()))
                return
            batch_page_ids = [
                row
                for row in session.execute(
                    select(Page.id).where(
                        Page.document_id == document_id,
                        Page.page_number.in_(page_numbers),
                    )
                ).scalars()
            ]
            _delete_page_children(session, batch_page_ids)

            batch_page_objs: dict[int, Page] = {}
            for number in page_numbers:
                page = (
                    session.execute(
                        select(Page).where(
                            Page.document_id == document_id,
                            Page.page_number == number,
                        )
                    )
                    .scalars()
                    .first()
                )
                if page is None:
                    page = Page(document_id=document_id, page_number=number)
                    session.add(page)
                page.status = "pending"
                page.error_message = None
                page.error_code = None
                page.width, page.height = _raster_size(parser.pages[number - 1])
                if _is_rotated(parser, number - 1):
                    page.page_image_key = f"documents/{document_id}/pages/{number}.png"
                if number - 1 in parser.manual_rotations:
                    page.manual_rotation_deg = parser.manual_rotations[number - 1]
                if number in thumbnail_keys:
                    page.thumbnail_key = thumbnail_keys[number]
                batch_page_objs[number] = page

            # Auto-rotation happens here, so the warning is recorded now;
            # parse_page sees only the upright raster. Manual-rotation warnings
            # come from parse_single_page via the seeded manual_rotations.
            session.flush()
            for number, page in batch_page_objs.items():
                angle = parser.page_orientations.get(number - 1)
                if angle:
                    session.add(
                        Warning(
                            page_id=page.id,
                            warning_type="quality",
                            message=f"Page was auto-rotated by {angle:.0f}°",
                        )
                    )

            queued = set(
                session.execute(
                    select(JobPage.page_number).where(JobPage.job_id == job.id)
                ).scalars()
            )
            now = _utcnow()
            for number in page_numbers:
                if number in queued:
                    continue
                session.add(
                    JobPage(
                        job_id=job.id,
                        page_number=number,
                        status="queued",
                        queued_at=now,
                    )
                )

        thumbnails_committed = True
    except BaseException:
        # This run's row write never committed. Delete only keys that no
        # earlier committed run still references.
        if not thumbnails_committed:
            _discard_thumbnails(document_id, list(thumbnail_keys.values()))
        raise

    _maybe_start_parsing(job_id)


@celery_app.task(
    bind=True,
    name="app.tasks.ingestion.parse_page",
    queue="ocr",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    soft_time_limit=600,
    time_limit=660,
)
def parse_page(self, job_id: str, page_no: int):
    """Parse one page (gateway call + post-processing) and commit its rows.

    Retry and failure are explicit (no autoretry): a retriable error with
    retries left raises self.retry; once exhausted (or on a permanent error)
    the page is marked failed so it always reaches a terminal state.
    """
    # 1. Delivery cap (poison pages): a page delivered more than
    # MAX_PAGE_DELIVERIES times (1 initial + 3 retries = 4 normal, so 6 leaves
    # headroom for crash redelivery) is marked failed without another gateway
    # call.
    count = _incr_page_delivery(job_id, page_no)
    if count is not None and count > settings.MAX_PAGE_DELIVERIES:
        _fail_page(
            job_id,
            page_no,
            "unknown",
            f"repeatedly crashed the worker (delivered {count} times)",
        )
        _bump_progress(job_id)
        return

    # 2. Early guards (cheap reads, no locks).
    with session_scope() as session:
        job = session.get(Job, UUID(job_id))
        if job is None or job.status == "canceled":
            return
        document = session.get(Document, job.document_id)
        if document is None:
            return
        page = (
            session.execute(
                select(Page).where(
                    Page.document_id == document.id, Page.page_number == page_no
                )
            )
            .scalars()
            .first()
        )
        document_id = document.id
        skip_text_layer = bool(job.skip_text_layer)
        manual_rotation = page.manual_rotation_deg if page is not None else None
        if page is not None and page.status in ("completed", "failed"):
            # A redelivered or late delivery of an already-terminal page: keep
            # counting (the cap above ran) but skip the work.
            _bump_progress(job_id)
            return
        # Record the processing transition + started_at.
        session.execute(
            update(JobPage)
            .where(
                JobPage.job_id == job.id,
                JobPage.page_number == page_no,
                JobPage.status == "queued",
            )
            .values(status="processing", started_at=_utcnow())
            .execution_options(synchronize_session=False)
        )

    # 3. Fetch the corrected raster. A missing key is a permanent failure.
    raster_key = f"documents/{document_id}/rasters/{page_no}.png"
    try:
        raster = get_storage().download_bytes(raster_key)
    except ObjectNotFoundError:
        _fail_page(
            job_id,
            page_no,
            "unknown",
            f"corrected raster not found in storage: {raster_key}",
        )
        _bump_progress(job_id)
        return

    # Staged text words, when the job allows them. A missing or unreadable
    # file leaves the parser on the ML path.
    text_layer_words = _load_text_layer_words(document_id, page_no, skip_text_layer)

    # 4. Per-page parse on a 1-page parser: page_index 0. A manual
    # override keeps auto-detection off.
    parser = DocumentParser()
    parser.text_layer_words = text_layer_words
    parser.pages = [base64.b64encode(raster).decode()]
    if manual_rotation is not None:
        parser.manual_rotations = {0: manual_rotation}
    with session_scope() as session:
        document = session.get(Document, document_id)
    try:
        objects = parser.parse_single_page(document, 0)
    except Exception as error:
        # An unexpected (non-gateway) failure while parsing the page.
        if _is_retriable(error) and self.request.retries < self.max_retries:
            raise self.retry(
                exc=error, countdown=_retry_backoff(self.request.retries)
            ) from error
        _fail_page(job_id, page_no, page_error_code(error), str(error))
        _bump_progress(job_id)
        return

    if parser.page_errors:
        # The gateway call for this page failed. Classify from the RAW
        # exception: 500/5xx/timeouts/connect are retried, 4xx are not.
        error = parser.page_errors[0]
        raw = error.raw
        if _is_retriable(raw) and self.request.retries < self.max_retries:
            raise self.retry(
                exc=raw, countdown=_retry_backoff(self.request.retries)
            ) from raw
        _fail_page(job_id, page_no, error.code, error.message)
        _bump_progress(job_id)
        return

    # 6. Success write, one transaction, NO document lock.
    try:
        _commit_success(job_id, page_no, document_id, parser, objects)
    except IntegrityError as error:
        pgcode = getattr(getattr(error, "orig", None), "pgcode", None)
        if pgcode in ("23503", "23505"):
            # 23503: the document was deleted mid-flight. 23505: a concurrent
            # delivery won the row. Both roll back and do not retry.
            logger.info(f"parse_page {job_id}:{page_no} rolled back ({pgcode})")
        else:
            raise

    # 8. Progress (idempotent, counts both terminal states).
    _bump_progress(job_id)


def _commit_success(
    job_id: str,
    page_no: int,
    document_id: UUID,
    parser: DocumentParser,
    objects: list,
) -> None:
    """Commit one successfully parsed page (and its children) in a single
    transaction that locks only the page row."""
    # Upload figure crops and any corrected page image BEFORE commit so a
    # committed page never points at a missing object.
    _upload_page_images(document_id, page_no, parser)

    with session_scope() as session:
        # Only the page row is locked (document -> job -> page order: page is
        # last). If it is already terminal a concurrent delivery won; drop the
        # result. If the job is canceled, do not save the user's results.
        page = (
            session.execute(
                select(Page)
                .where(Page.document_id == document_id, Page.page_number == page_no)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if page is None or page.status in ("completed", "failed"):
            return
        job = session.get(Job, UUID(job_id))
        if job is None or job.status == "canceled":
            return

        _delete_page_children(session, [page.id], delete_warnings=False)

        new_page = next((o for o in objects if isinstance(o, Page)), None)
        if new_page is None:
            return
        # Keep the stable page id: re-point the freshly built children at the
        # existing row so the viewer's per-page identity is unchanged. The 1-page
        # parser built them as page 1, so also fix their page number.
        for obj in objects:
            if isinstance(obj, (Region, Warning)):
                obj.page_id = page.id
            if isinstance(obj, Region):
                obj.page_number = page_no
        # Copy the parse results onto the existing row. page_image_key stays as
        # ingest set it (the corrected key if rotated, else unchanged);
        # thumbnail_key was set by ingest.
        page.width = new_page.width
        page.height = new_page.height
        page.model_name = new_page.model_name
        page.model_version = new_page.model_version
        page.status = "completed"
        page.error_code = None
        page.error_message = None
        # Transition the run's job_page to completed (the live progress source);
        # the failure path already does this in _fail_page. Guarded on open
        # statuses so it never clobbers a job_page the sweeper canceled.
        session.execute(
            update(JobPage)
            .where(
                JobPage.job_id == job.id,
                JobPage.page_number == page_no,
                JobPage.status.in_(OPEN_PAGE_STATUSES),
            )
            .values(status="completed", finished_at=_utcnow())
            .execution_options(synchronize_session=False)
        )
        for obj in objects:
            if not isinstance(obj, Page):
                session.add(obj)


def _upload_page_images(
    document_id: UUID, page_no: int, parser: DocumentParser
) -> None:
    for key, image in parser.figure_images.items():
        try:
            get_storage().upload_file(io.BytesIO(image), key)
        except Exception:
            logger.exception(f"Could not store figure {key}")
    # parse_single_page keyed the corrected image by page_index 1; re-key it to
    # the real page number.
    for image in parser.page_images.values():
        try:
            get_storage().upload_file(
                io.BytesIO(image), f"documents/{document_id}/pages/{page_no}.png"
            )
        except Exception:
            logger.exception(f"Could not store corrected page {page_no}")


def _fail_page(job_id: str, page_no: int, error_code: str, error_message: str) -> None:
    """Mark one page failed (terminal) with the given error code/message."""
    with session_scope() as session:
        job = session.get(Job, UUID(job_id))
        if job is None or job.status == "canceled":
            return
        document = session.get(Document, job.document_id)
        if document is None:
            return
        page = (
            session.execute(
                select(Page)
                .where(Page.document_id == document.id, Page.page_number == page_no)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if page is None or page.status in ("completed", "failed"):
            return
        page.status = "failed"
        page.error_code = error_code
        page.error_message = error_message
        now = _utcnow()
        session.execute(
            update(JobPage)
            .where(
                JobPage.job_id == job.id,
                JobPage.page_number == page_no,
                JobPage.status.in_(OPEN_PAGE_STATUSES),
            )
            .values(
                status="failed",
                error_code=error_code,
                error_message=error_message,
                finished_at=now,
            )
            .execution_options(synchronize_session=False)
        )


@celery_app.task(
    name="app.tasks.ingestion.finalize_document",
    queue="default",
    soft_time_limit=300,
    time_limit=330,
    acks_late=False,
)
def finalize_document(job_id: str):
    """Finish a run: stitch tables, derive document status, and clean up.

    Idempotent, so duplicate enqueues are safe. Failure is a
    data write, not an exception: the job fails only when zero of its run pages
    completed.
    """
    document = None
    run_numbers: list[int] = []
    with session_scope() as session:
        job = session.get(Job, UUID(job_id))
        if job is None:
            return
        document = session.get(Document, job.document_id, with_for_update=True)
        if document is None:
            return
        session.refresh(job, with_for_update=True)
        # Idempotency guard: canceled, already finished, or not all run pages
        # terminal yet.
        if job.status == "canceled" or job.current_stage == "finished":
            return
        job_pages = (
            session.execute(select(JobPage).where(JobPage.job_id == job.id))
            .scalars()
            .all()
        )
        terminal = sum(1 for jp in job_pages if jp.status in ("completed", "failed"))
        if job.total_pages and terminal < job.total_pages:
            return

        # Cross-page stitching over committed rows: repair tables that lost a
        # middle page (drop orphaned empties), then merge consecutive
        # standalone tables only when the job asked for it.
        tables = (
            session.execute(select(Table).where(Table.document_id == document.id))
            .scalars()
            .all()
        )
        for table in tables:
            split_at_gaps(session, table)
        if job.merge_consecutive_tables:
            _stitch_document_tables(session, document.id)

        # Derive the document's status from all of its page rows.
        page_states = session.execute(
            select(Page.page_number, Page.status).where(Page.document_id == document.id)
        ).all()
        run_completed = sum(1 for jp in job_pages if jp.status == "completed")
        run_failed = bool(job_pages) and not run_completed

        # The run's model is the majority of its completed pages.
        page_models = {}
        for jp in job_pages:
            if jp.status != "completed":
                continue
            page = (
                session.execute(
                    select(Page).where(
                        Page.document_id == document.id,
                        Page.page_number == jp.page_number,
                    )
                )
                .scalars()
                .first()
            )
            if page is not None and page.model_name and page.model_version:
                page_models[jp.page_number] = ModelInfo(
                    page.model_name, page.model_version
                )
        run_model = _run_model(page_models)

        # The run's failed-page summary is kept on the job whenever any run page
        # failed. run_failed only
        # decides the job's status, not whether the summary is recorded. The
        # pages are sorted so the summary is deterministic and matches the
        # document's summary.
        failed_run_pages = sorted(
            jp.page_number for jp in job_pages if jp.status == "failed"
        )
        summary = (
            _describe_failed_pages(failed_run_pages, len(job_pages))
            if failed_run_pages
            else None
        )
        failed_codes = [jp.error_code for jp in job_pages if jp.status == "failed"]
        error_stage = None
        if run_failed:
            error_stage = (
                "queue_timeout"
                if any(code == "queue_timeout" for code in failed_codes)
                else "parsing"
            )

        job.model_name = run_model.name if run_model else None
        job.model_version = run_model.version if run_model else None
        job.pipeline_version = PIPELINE_VERSION
        job.error_message = summary
        job.error_stage = error_stage
        job.current_stage = "failed" if run_failed else "finished"
        job.status = "failed" if run_failed else "completed"
        job.pages_processed = terminal

        document.status, document.error_message = _document_state(
            page_states, document.page_count or len(page_states)
        )
        if run_model is not None:
            document.model_name = job.model_name
            document.model_version = job.model_version
            document.pipeline_version = job.pipeline_version
            document.processed_at = _utcnow()

        _reconcile_job_pages(session, str(job.id))
        run_numbers = [jp.page_number for jp in job_pages]

    # Best-effort delete the run pages' rasters (NEVER pages/*), reconstructing
    # each key from the job's run page numbers (storage has no list delete).
    if document is not None:
        _delete_stored(
            [f"documents/{document.id}/rasters/{n}.png" for n in run_numbers]
        )


def _stitch_document_tables(session: Session, document_id: UUID) -> None:
    """Merge standalone tables that sit at the end of one page and the start of
    the next.

    merge_two_tables reassigns RegionTable.table, so a part's table
    relationship is refreshed before each use (the session otherwise keeps the
    stale relationship).
    """
    table_ids = [
        row
        for row in session.execute(
            select(Table.id).where(Table.document_id == document_id)
        ).scalars()
    ]
    for table_id in table_ids:
        table = session.get(Table, table_id)
        if table is None:
            continue  # merged into another table and deleted
        part_ids = [
            row
            for row in session.execute(
                select(RegionTable.id).where(RegionTable.table_id == table.id)
            ).scalars()
        ]
        for part_id in part_ids:
            part = session.get(RegionTable, part_id)
            if part is None:
                continue
            region = part.region
            if region is None or region.page is None:
                continue
            page = region.page
            prev_page = (
                session.execute(
                    select(Page).where(
                        Page.document_id == document_id,
                        Page.page_number == page.page_number - 1,
                    )
                )
                .scalars()
                .first()
            )
            next_page = (
                session.execute(
                    select(Page).where(
                        Page.document_id == document_id,
                        Page.page_number == page.page_number + 1,
                    )
                )
                .scalars()
                .first()
            )
            prev_part = (
                _edge_region_table(session, prev_page, end=True)
                if prev_page is not None
                else None
            )
            if prev_part is not None and prev_part.id != part.id:
                session.refresh(prev_part, attribute_names=["table"])
                if is_consecutive(session, prev_part, part):
                    merge_two_tables(session, prev_part.table, table)
            next_part = (
                _edge_region_table(session, next_page, end=False)
                if next_page is not None
                else None
            )
            if next_part is not None and next_part.id != part.id:
                session.refresh(next_part, attribute_names=["table"])
                session.refresh(part, attribute_names=["table"])
                if is_consecutive(session, part, next_part):
                    merge_two_tables(session, part.table, next_part.table)


def _edge_region_table(session: Session, page, end: bool):
    """The region table at the page's reading-order edge (max if end, min if
    not)."""
    order_col = (
        func.max(Region.reading_order) if end else func.min(Region.reading_order)
    )
    order = session.execute(select(order_col).where(Region.page_id == page.id)).scalar()
    if order is None:
        return None
    return (
        session.execute(
            select(RegionTable)
            .join(Region)
            .where(Region.page_id == page.id, Region.reading_order == order)
        )
        .scalars()
        .first()
    )


@celery_app.task(
    name="app.tasks.ingestion.sweep_stuck_jobs",
    queue="default",
    soft_time_limit=600,
    time_limit=660,
    acks_late=False,
)
def sweep_stuck_jobs():
    """Beat sweeper: re-enqueue stuck work. Every op is idempotent.

    Rate-limited per job so a job legitimately stuck
    behind a large upload is not re-enqueued every 2 minutes.
    """
    now = _utcnow()
    with session_scope() as session:
        for job in session.execute(
            select(Job).where(
                Job.status == "processing",
                Job.current_stage.in_(
                    ("downloading", "preparing", "parsing", "saving")
                ),
            )
        ).scalars():
            if _is_swept(str(job.id)):
                continue
            _sweep_one(session, job, now)


def _sweep_one(session: Session, job: Job, now: datetime) -> None:
    """Apply the sweep rules to one job, then rate-limit it.

    The rules are independent within a stage: a job can both time out queued
    pages and be stale enough to re-enqueue its processing pages.
    """
    job_pages = (
        session.execute(select(JobPage).where(JobPage.job_id == job.id)).scalars().all()
    )
    terminal = sum(1 for jp in job_pages if jp.status in ("completed", "failed"))
    acted = False

    if job.current_stage == "parsing":
        if job.total_pages and terminal >= job.total_pages:
            # 1. Missing finalize: every run page is terminal but the job is
            # still parsing -> enqueue finalize. There are no queued or
            # processing pages to act on, so the other rules cannot also apply.
            _enqueue_finalize(str(job.id))
            acted = True
        else:
            # 2. Queue timeout: a page still queued beyond the timeout is failed
            # (error_code=queue_timeout), then the recount may trigger finalize.
            timed_out = [
                jp
                for jp in job_pages
                if jp.status == "queued"
                and jp.queued_at is not None
                and (now - jp.queued_at).total_seconds()
                > settings.PAGE_QUEUE_TIMEOUT_SECONDS
            ]
            failed_count = 0
            for jp in timed_out:
                if _expire_queued_page(session, job, jp, now):
                    failed_count += 1
            if failed_count:
                _recount_progress(session, str(job.id))
                acted = True
                # Failing the timed-out page may have made every run page
                # terminal; finalize it promptly rather than wait a sweep.
                if terminal + failed_count >= job.total_pages:
                    _enqueue_finalize(str(job.id))

            # 3. Stalled processing pages: the job has not advanced in a while,
            # so re-enqueue the pages stuck processing.
            if _is_stale(job, now):
                for jp in job_pages:
                    if jp.status == "processing":
                        _enqueue_parse_page(str(job.id), jp.page_number)
                acted = True
    elif (
        job.current_stage in ("downloading", "preparing")
        and job.updated_at is not None
        and (now - job.updated_at).total_seconds() > 15 * 60
    ):
        # 4. Stuck ingest: a job that never left downloading/preparing is
        # re-enqueued.
        celery_app.send_task(
            "app.tasks.ingestion.ingest_document", args=[str(job.id)], queue="default"
        )
        acted = True

    if acted:
        _mark_swept(str(job.id))


def _expire_queued_page(session: Session, job: Job, jp: JobPage, now: datetime) -> bool:
    """Fail a timed-out queued page, guarding against a concurrent delivery.

    A parse_page _commit_success can complete the page between this sweep's
    unlocked read of job_pages and this write. Re-check the JobPage's status and
    queued_at, then lock the Page row (document -> job -> page order: page last)
    and confirm it is not terminal before failing it. The JobPage write is a
    guarded UPDATE so a row a concurrent task moved off queued is left alone.

    Returns True only when this call actually failed the page.
    """
    expected_queued_at = jp.queued_at
    fresh = session.execute(
        select(JobPage.status, JobPage.queued_at).where(JobPage.id == jp.id)
    ).first()
    if fresh is None or fresh[0] != "queued" or fresh[1] != expected_queued_at:
        return False

    page = _page_row(session, job, jp.page_number, for_update=True)
    if page is not None and page.status in ("completed", "failed"):
        return False

    error_message = "Page waited too long in the processing queue and was not started"
    result = session.execute(
        update(JobPage)
        .where(
            JobPage.id == jp.id,
            JobPage.status == "queued",
            JobPage.queued_at == expected_queued_at,
        )
        .values(
            status="failed",
            error_code="queue_timeout",
            error_message=error_message,
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False

    if page is not None:
        page.status = "failed"
        page.error_code = "queue_timeout"
        page.error_message = error_message
    return True


def _page_row(session: Session, job: Job, page_number: int, for_update: bool = False):
    stmt = select(Page).where(
        Page.document_id == job.document_id, Page.page_number == page_number
    )
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalars().first()


def _is_stale(job: Job, now: datetime) -> bool:
    return (
        job.updated_at is not None
        and (now - job.updated_at).total_seconds() > settings.PAGE_STALE_SECONDS
    )
