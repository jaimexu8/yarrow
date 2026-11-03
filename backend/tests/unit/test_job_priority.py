"""US-92: smaller files get a higher celery queue priority."""

from uuid import uuid4

import pytest

from app.core.queue import (
    PROCESS_DOCUMENT_TASK,
    enqueue_document_processing,
    get_celery,
    job_priority,
)


@pytest.mark.parametrize(
    "size, expected",
    [
        (0, 0),
        (999_999, 0),
        (1_000_000, 3),
        (9_999_999, 3),
        (10_000_000, 7),
        (100_000_000, 7),
    ],
)
def test_job_priority_buckets(size, expected):
    assert job_priority(size) == expected


def test_a_small_file_outranks_a_large_one():
    assert job_priority(500_000) < job_priority(15_000_000)


def test_backend_client_enables_redis_priorities():
    get_celery.cache_clear()
    opts = get_celery().conf.broker_transport_options
    assert opts["queue_order_strategy"] == "priority"
    assert list(opts["priority_steps"]) == list(range(10))


def test_enqueue_passes_priority_to_send_task(monkeypatch):
    sent = {}

    class _Result:
        id = "task-1"

    class _Celery:
        def send_task(self, name, **kwargs):
            sent["name"] = name
            sent.update(kwargs)
            return _Result()

    monkeypatch.setattr("app.core.queue.get_celery", lambda: _Celery())
    job_id = uuid4()

    enqueue_document_processing(job_id, pages=[1, 2], priority=3)

    assert sent["name"] == PROCESS_DOCUMENT_TASK
    assert sent["kwargs"] == {"job_id": str(job_id), "page_to_process": [1, 2]}
    assert sent["priority"] == 3


def test_enqueue_omits_priority_when_none(monkeypatch):
    sent = {}

    class _Result:
        id = "task-1"

    class _Celery:
        def send_task(self, name, **kwargs):
            sent.update(kwargs)
            return _Result()

    monkeypatch.setattr("app.core.queue.get_celery", lambda: _Celery())

    enqueue_document_processing(uuid4())

    assert "priority" not in sent
