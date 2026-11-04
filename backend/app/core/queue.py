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

# redis list names use this separator (celery:0, celery:3, ...).
_REDIS_PRIORITY_OPTIONS = {
    "priority_steps": list(range(10)),
    "sep": ":",
    "queue_order_strategy": "priority",
}


def job_priority(file_size_bytes: int) -> int:
    """Map file size to a celery redis priority.

    0 is highest, 9 is lowest. smaller files run first so a short scan is
    not stuck behind a huge one (us-92).
    thresholds: under 1_000_000 bytes -> 0, under 10_000_000 -> 3, else 7.
    """
    if file_size_bytes < 1_000_000:
        return 0
    if file_size_bytes < 10_000_000:
        return 3
    return 7


@lru_cache(maxsize=1)
def get_celery() -> Celery:
    app = Celery("yarrow-backend", broker=settings.CELERY_BROKER_URL)
    # redis ignores send_task priority unless the worker and the client both
    # set priority_steps and queue_order_strategy.
    app.conf.broker_transport_options = dict(_REDIS_PRIORITY_OPTIONS)
    return app


def enqueue_document_processing(
    job_id: UUID,
    pages: Sequence[int] | None = None,
    priority: int | None = None,
) -> str:
    """Queue one ingestion job and return the Celery task id.

    pages limits the job to those 1-based page numbers, keeping every other
    page's results. None processes the whole document.

    priority is a celery redis priority (0 highest, 9 lowest). callers pass
    job_priority of the file size so small files run first.

    Called only after the job row is committed: the worker can pick the message
    up within milliseconds, and a task that starts before its row is visible
    looks up a job that does not exist.
    """
    kwargs: dict = {"job_id": str(job_id)}
    if pages is not None:
        kwargs["page_to_process"] = list(pages)
    send_kwargs: dict = {"kwargs": kwargs}
    if priority is not None:
        send_kwargs["priority"] = priority
    result = get_celery().send_task(PROCESS_DOCUMENT_TASK, **send_kwargs)
    return result.id


def revoke_document_processing(task_id: str, terminate: bool = False) -> None:
    """Ask workers to drop a queued task (US-42).

    Best effort: Celery keeps revocations in worker memory only, so a worker
    that restarts forgets them. The job row marked "canceled" is what really
    stops processing; the worker checks it before starting.

    terminate also stops a task that is already running, by sending SIGTERM to
    the worker process executing it, which frees its pool slot. Admin
    force-cancel uses it (US-75); a user cancel lets the run reach its next
    check instead.
    """
    if terminate:
        get_celery().control.revoke(task_id, terminate=True, signal="SIGTERM")
        return
    get_celery().control.revoke(task_id)
