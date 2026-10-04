import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, Page

from app.core.config import settings
from app.schemas.document import DocumentOut, ReprocessInfo
from app.services.failure_messages import INTERRUPTED_MESSAGE, QUEUE_FAILED_MESSAGE

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATES = frozenset({"queued", "processing"})
# How many page numbers ReprocessInfo lists for display.
PAGE_NUMBERS_SHOWN = 10


def is_interrupted(job: dict[str, str | datetime | None] | None, now: datetime) -> bool:
    """Whether a queued or processing job has been silent for too long"""
    if job is None or job["status"] not in ACTIVE_JOB_STATES:
        return False
    if job["updated_at"] is None:
        return True
    return now - job["updated_at"] > timedelta(seconds=settings.JOB_INTERRUPTED_AFTER_SECONDS)


def is_running(job: dict[str, str | datetime | None] | None, now: datetime) -> bool:
    """A job is queued or processing and still alive."""
    return job is not None and job["status"] in ACTIVE_JOB_STATES and not is_interrupted(job, now)


def interrupted(document_status: str | None, job: dict[str, str | datetime | None] | None, now: datetime) -> bool:
    """The document says it is in progress, but nothing is working on it."""
    if is_interrupted(job, now):
        return True
    job_active = job is not None and job["status"] in ACTIVE_JOB_STATES
    return document_status in ACTIVE_JOB_STATES and not job_active


def reprocess_scope(
    document_status: str | None,
    page_count: int | None,
    completed_pages: int,
    job: dict[str, str | datetime | None] | None,
    now: datetime,
) -> Literal["incomplete", "all"] | None:
    """What reprocessing would cover, or None while a job is still running"""
    if is_running(job, now):
        return None
    if interrupted(document_status, job, now) or document_status == "failed":
        return "incomplete"
    if document_status == "completed" and page_count is not None and completed_pages < page_count:
        return "incomplete"
    # Fully completed, or canceled before anything was kept.
    return "all"


def pages_to_process(page_count: int | None, completed: Iterable[int]) -> list[int] | None:
    if page_count is None:
        return None
    done = set(completed)
    return [number for number in range(1, page_count + 1) if number not in done]


async def reprocess_info_for(db: AsyncSession, documents: Sequence[Document]) -> dict[UUID, ReprocessInfo | None]:
    """ReprocessInfo for each document, in a few queries however many there are."""
    if not documents:
        return {}
    ids = [document.id for document in documents]

    # Gets jobs for each document, keyed by document_id
    job_rows = await db.execute(
        select(Job.document_id, Job.status, Job.updated_at)
        .where(Job.document_id.in_(ids))
        .distinct(Job.document_id)
        .order_by(Job.document_id, Job.created_at.desc())
    )
    jobs = {row.document_id: {"status": row.status, "updated_at": row.updated_at} for row in job_rows}

    # Counts for completed pages per document
    completed_counts = dict(
        (
            await db.execute(
                select(Page.document_id, func.count()).where(Page.document_id.in_(ids), Page.status == "completed").group_by(Page.document_id)
            )
        ).all()
    )

    now = datetime.now(UTC).replace(tzinfo=None)

    # Determine the reprocessing scope for each document
    scopes = {
        document.id: reprocess_scope(
            document.status,
            document.page_count,
            completed_counts.get(document.id, 0),
            jobs.get(document.id),
            now,
        )
        for document in documents
    }

    # Page numbers only for the documents whose unfinished pages are listed.
    numbered = [document.id for document in documents if scopes[document.id] == "incomplete" and document.page_count is not None]
    completed: dict[UUID, list[int]] = {}
    if numbered:
        rows = await db.execute(select(Page.document_id, Page.page_number).where(Page.document_id.in_(numbered), Page.status == "completed"))
        for row in rows:
            completed.setdefault(row.document_id, []).append(row.page_number)

    # Populate the ReprocessInfo for each document
    info: dict[UUID, ReprocessInfo | None] = {}
    for document in documents:
        scope = scopes[document.id]
        if scope is None:
            info[document.id] = None
            continue
        pages = (
            pages_to_process(document.page_count, completed.get(document.id, []))
            if scope == "incomplete"
            else pages_to_process(document.page_count, [])
        )
        info[document.id] = ReprocessInfo(
            scope=scope,
            interrupted=interrupted(document.status, jobs.get(document.id), now),
            pages=None if pages is None else len(pages),
            page_numbers=(pages or [])[:PAGE_NUMBERS_SHOWN] if scope == "incomplete" else [],
        )
    return info


async def documents_out(db: AsyncSession, documents: Sequence[Document]) -> list[DocumentOut]:
    """DocumentOut for each document, with its reprocess info filled in."""
    info = await reprocess_info_for(db, documents)
    out = []
    for document in documents:
        item = DocumentOut.model_validate(document)
        item.reprocess = info[document.id]
        out.append(item)
    return out


class ReprocessError(str, Enum):
    ALREADY_PROCESSING = "ALREADY_PROCESSING"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"


@dataclass
class ReprocessOutcome:
    error: ReprocessError | None = None
    detail: str | None = None
    scope: Literal["incomplete", "all"] | None = None
    # The 1-based pages queued, or None for the whole document.
    pages: list[int] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def reprocess_document(
    db: AsyncSession,
    document_id: UUID,
    enqueue: Callable[[UUID, Sequence[int] | None], str],
) -> ReprocessOutcome:
    """Queue a job that reprocesses the document; see the module docstring
    for which pages it covers
    """
    job = (
        await db.execute(
            select(Job)
            .where(Job.document_id == document_id)
            .order_by(Job.created_at.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC).replace(tzinfo=None)
    latest: dict[str, str | datetime | None] | None = {"status": job.status, "updated_at": job.updated_at} if job else None

    # If the latest job is still running, we cannot reprocess the document yet
    if is_running(latest, now):
        await db.rollback()
        return ReprocessOutcome(
            error=ReprocessError.ALREADY_PROCESSING,
            detail="This document is already being processed.",
        )

    document = (
        await db.execute(select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True))
    ).scalar_one()
    completed = list(await db.scalars(select(Page.page_number).where(Page.document_id == document_id, Page.status == "completed")))
    scope = reprocess_scope(document.status, document.page_count, len(completed), latest, now)
    pages = pages_to_process(document.page_count, completed) if scope == "incomplete" else None

    if job is not None and job.status in ACTIVE_JOB_STATES:
        # Job is active but not running, indicates that it was interrupted or failed mid-run.
        job.status = "failed"
        job.error_message = INTERRUPTED_MESSAGE

    previous_state = (document.status, document.error_message)
    
    new_job = Job(
        id=uuid4(),
        document_id=document_id,
        status="queued",
        current_stage="queued",
        pages_processed=0,
        total_pages=len(pages) if pages is not None else (document.page_count or 0),
    )
    
    db.add(new_job)
    document.status = "queued"
    document.error_message = None
    
    await db.commit()

    try:
        # Enqueue the reprocess job in the background worker
        task_id = await run_in_threadpool(enqueue, new_job.id, pages)
    except Exception:
        logger.exception(f"Queueing reprocess job {new_job.id} failed")
        new_job.status = "failed"
        new_job.error_message = QUEUE_FAILED_MESSAGE
        # Nothing was started, so the document is exactly as it was.
        document.status, document.error_message = previous_state
        await db.commit()
        return ReprocessOutcome(
            error=ReprocessError.QUEUE_UNAVAILABLE,
            detail="Processing could not be started. Please try again later.",
        )

    new_job.celery_task_id = task_id
    await db.commit()
    return ReprocessOutcome(scope=scope, pages=pages)
