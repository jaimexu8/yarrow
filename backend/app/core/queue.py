"""The backend's handle on the Celery queue.

A plain Celery client, not the worker's app: the backend image does not install
the worker package, so tasks are dispatched by name with send_task rather than
imported. The name must match the worker's task name exactly.
"""

from collections.abc import Sequence
from functools import lru_cache
from uuid import UUID

from celery import Celery

from .config import settings

PROCESS_DOCUMENT_TASK = "app.tasks.ingestion.process_document_task"


@lru_cache(maxsize=1)
def get_celery() -> Celery:
    return Celery("yarrow-backend", broker=settings.CELERY_BROKER_URL)


def enqueue_document_processing(
    job_id: UUID, pages: Sequence[int] | None = None
) -> str:
    """Queue one ingestion job and return the Celery task id.

    pages limits the job to those 1-based page numbers, keeping every other
    page's results. None processes the whole document.

    Called only after the job row is committed: the worker can pick the message
    up within milliseconds, and a task that starts before its row is visible
    looks up a job that does not exist.
    """
    kwargs: dict = {"job_id": str(job_id)}
    if pages is not None:
        kwargs["page_to_process"] = list(pages)
    result = get_celery().send_task(PROCESS_DOCUMENT_TASK, kwargs=kwargs)
    return result.id


def revoke_document_processing(task_id: str) -> None:
    """Ask workers to drop a queued task (US-42).

    Best effort: Celery keeps revocations in worker memory only, so a worker
    that restarts forgets them. The job row marked "canceled" is what really
    stops processing; the worker checks it before starting.
    """
    get_celery().control.revoke(task_id)
