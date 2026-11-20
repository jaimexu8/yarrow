"""Phase 2 Celery wiring: queue routing, delivery guarantees, and beat."""

from app.celery_app import celery_app
from app.tasks.ingestion import finalize_document, ingest_document, parse_page


def test_task_queues():
    assert ingest_document.queue == "default"
    assert finalize_document.queue == "default"
    assert parse_page.queue == "ocr"


def test_parse_page_delivery_guarantees():
    assert parse_page.acks_late is True
    assert parse_page.reject_on_worker_lost is True


def test_default_queue_and_prefetch():
    assert celery_app.conf.task_default_queue == "default"
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_broker_visibility_timeout():
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 3600


def test_beat_sweeper_schedule():
    assert celery_app.conf.beat_schedule["sweep-stuck-jobs"]["schedule"] == 120


def test_parse_page_route():
    assert celery_app.conf.task_routes["app.tasks.ingestion.parse_page"] == {
        "queue": "ocr"
    }
