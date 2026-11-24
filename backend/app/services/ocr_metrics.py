"""Operator metrics for the OCR pipeline, served by GET /admin/ocr/metrics.

The queue depth comes from the broker, the rest from the database. The broker
read is injected as a plain int so the database half is testable without a
live broker.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Job, JobPage

from app.schemas.admin import OcrMetrics

# The broker's redis transport splits each queue into one list per priority
# step. This mirrors the priority options the backend sends in app.core.queue
# (_REDIS_PRIORITY_OPTIONS: priority_steps 0..9, sep ":").
PRIORITY_STEPS = tuple(range(10))
PRIORITY_SEP = ":"

# Job statuses that still own live queue work.
ACTIVE_JOB_STATUSES = ("queued", "processing")

# error_codes that count as a rejected page in the operator metrics.
REJECTED_ERROR_CODES = ("inference_unavailable", "queue_timeout")

REJECTION_WINDOW = timedelta(minutes=5)


def _queue_list_names(queue: str) -> list[str]:
    """Broker list names that hold messages for ``queue``.

    kombu's redis transport names the priority-0 list as the bare queue name
    (``ocr``) and each higher priority as ``{queue}:{priority}`` (``_q_for_pri``
    returns the queue name unchanged when the priority is falsy). Messages sent
    without an explicit priority (as ``parse_page`` are) land in the bare list,
    so the full set is ``ocr`` + ``ocr:1`` .. ``ocr:9``.
    """
    return [queue] + [
        f"{queue}{PRIORITY_SEP}{priority}" for priority in range(1, len(PRIORITY_STEPS))
    ]


def _queue_depth(redis_client, queue: str = "ocr") -> int:
    """Pages currently waiting on ``queue``.

    Sums LLEN over every broker list the queue uses (the bare ``ocr`` list for
    priority 0, plus ``ocr:1`` .. ``ocr:9``).
    """
    return sum(redis_client.llen(name) for name in _queue_list_names(queue))


async def collect_ocr_metrics(db: AsyncSession, queue_depth: int) -> OcrMetrics:
    """The four database-side metrics for the OCR pipeline.

    ``queue_depth`` is computed by the caller (it needs the broker). All
    timestamps are compared against the codebase's naive-UTC now, computed in
    Python to match the rest of the backend.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    active_jobs = select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES))

    pages_queued = int(
        (
            await db.execute(
                select(func.count(JobPage.id)).where(
                    JobPage.status == "queued",
                    JobPage.job_id.in_(active_jobs),
                )
            )
        ).scalar_one()
    )
    pages_processing = int(
        (
            await db.execute(
                select(func.count(JobPage.id)).where(
                    JobPage.status == "processing",
                    JobPage.job_id.in_(active_jobs),
                )
            )
        ).scalar_one()
    )
    oldest_queued = (
        await db.execute(
            select(func.min(JobPage.queued_at)).where(
                and_(
                    JobPage.status == "queued",
                    JobPage.queued_at.is_not(None),
                    JobPage.job_id.in_(active_jobs),
                )
            )
        )
    ).scalar_one_or_none()
    rejected_last_5m = int(
        (
            await db.execute(
                select(func.count(JobPage.id)).where(
                    JobPage.status == "failed",
                    JobPage.error_code.in_(REJECTED_ERROR_CODES),
                    JobPage.finished_at >= now - REJECTION_WINDOW,
                )
            )
        ).scalar_one()
    )

    return OcrMetrics(
        queue_depth=queue_depth,
        pages_queued=pages_queued,
        pages_processing=pages_processing,
        oldest_queued_seconds=(
            (now - oldest_queued).total_seconds() if oldest_queued is not None else None
        ),
        rejected_last_5m=rejected_last_5m,
    )
