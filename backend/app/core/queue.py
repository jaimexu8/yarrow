"""The backend's handle on the Celery queue.

A plain Celery client, not the worker's app: the backend image does not install
the worker package, so tasks are dispatched by name with send_task rather than
imported. The name must match the worker's task name exactly.
"""

from functools import lru_cache
from uuid import UUID

from celery import Celery

from .config import settings

PROCESS_DOCUMENT_TASK = "app.tasks.ingestion.process_document_task"


@lru_cache(maxsize=1)
def get_celery() -> Celery:
    return Celery("yarrow-backend", broker=settings.CELERY_BROKER_URL)


def enqueue_document_processing(job_id: UUID) -> str:
    """Queue one ingestion job and return the Celery task id.

    Called only after the job row is committed: the worker can pick the message
    up within milliseconds, and a task that starts before its row is visible
    looks up a job that does not exist.
    """
    result = get_celery().send_task(
        PROCESS_DOCUMENT_TASK, kwargs={"job_id": str(job_id)}
    )
    return result.id
