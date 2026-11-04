from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import Update, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import (
    OPEN_PAGE_STATUSES,
    PAGE_STATUSES,
    Document,
    Job,
    JobPage,
    Page,
)

from app.schemas.progress import DocumentProgress, PageProgress
from app.services.failure_messages import page_error_message

ACTIVE_JOB_STATUSES = ("queued", "processing")
TERMINAL_JOB_STATUSES = ("completed", "failed", "canceled")


def cancel_open_job_pages(job_ids: Collection[UUID], now: datetime) -> Update:
    return (
        update(JobPage)
        .where(
            JobPage.job_id.in_(list(job_ids)),
            JobPage.status.in_(OPEN_PAGE_STATUSES),
        )
        .values(status="canceled", finished_at=now)
        .execution_options(synchronize_session=False)
    )


def progress_percent(job_status: str | None, pages_total: int, pages_finished: int) -> int:
    if job_status in TERMINAL_JOB_STATUSES:
        return 100
    if pages_total <= 0:
        return 0
    return min(100, max(0, (100 * pages_finished) // pages_total))


def _page_progress(
    page_number: int,
    status: str | None,
    error_code: str | None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> PageProgress:
    if status not in PAGE_STATUSES:
        status = None
    return PageProgress(
        page_number=page_number,
        status=status,
        error_code=error_code,
        error_message=page_error_message(error_code, status),
        started_at=started_at,
        finished_at=finished_at,
    )


async def document_progress(db: AsyncSession, document: Document) -> DocumentProgress:
    job = (
        await db.execute(
            select(Job)
            .where(Job.document_id == document.id)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    job_pages: list[JobPage] = []
    if job is not None:
        job_pages = list(
            await db.scalars(
                select(JobPage)
                .where(JobPage.job_id == job.id)
                .order_by(JobPage.page_number)
            )
        )

    page_rows = (
        await db.execute(
            select(Page.page_number, Page.status, Page.error_code).where(
                Page.document_id == document.id
            )
        )
    ).all()

    job_status = job.status if job is not None else None
    pages_total = (job.total_pages or 0) if job is not None else 0
    pages_completed = sum(page.status == "completed" for page in job_pages)
    pages_failed = sum(page.status == "failed" for page in job_pages)

    live_pages = (
        {page.page_number: page for page in job_pages}
        if job_status in ACTIVE_JOB_STATUSES
        else {}
    )
    stored_pages = {row.page_number: row for row in page_rows}
    last_page = max(
        document.page_count or 0,
        pages_total,
        max((page.page_number for page in job_pages), default=0),
    )

    pages: list[PageProgress] = []
    for page_number in range(1, last_page + 1):
        live = live_pages.get(page_number)
        stored = stored_pages.get(page_number)
        if live is not None:
            pages.append(
                _page_progress(
                    page_number,
                    live.status,
                    live.error_code,
                    live.started_at,
                    live.finished_at,
                )
            )
        elif stored is not None:
            pages.append(_page_progress(page_number, stored.status, stored.error_code))
        else:
            pages.append(_page_progress(page_number, None, None))

    return DocumentProgress(
        document_id=document.id,
        document_status=document.status,
        job_id=job.id if job is not None else None,
        job_status=job_status,
        current_stage=job.current_stage if job is not None else None,
        pages_total=pages_total,
        pages_completed=pages_completed,
        pages_failed=pages_failed,
        progress_percent=progress_percent(
            job_status, pages_total, pages_completed + pages_failed
        ),
        updated_at=job.updated_at if job is not None else None,
        pages=pages,
    )
