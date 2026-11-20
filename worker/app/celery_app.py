import os

from celery import Celery

# Broker and result backend live on their own Valkey databases (1 and 2) so
# queue traffic never shares a database with the auth data on database 0.
broker_url = os.getenv("CELERY_BROKER_URL", "redis://valkey:6379/1")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://valkey:6379/2")

celery_app = Celery(
    "worker", broker=broker_url, backend=result_backend, include=["app.tasks.ingestion"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Page parses land on a dedicated queue; everything else uses the default.
    task_default_queue="default",
    task_routes={
        "app.tasks.ingestion.parse_page": {"queue": "ocr"},
    },
    # One task per worker at a time, so an idle worker never hoards queued pages.
    worker_prefetch_multiplier=1,
    beat_schedule={
        "sweep-stuck-jobs": {
            "task": "app.tasks.ingestion.sweep_stuck_jobs",
            "schedule": 120,
        },
    },
    # redis ignores send_task priority unless these are set. 0 is highest.
    # visibility_timeout re-hides a message if its worker dies with the task
    # still unacked, instead of dropping it. Note the asymmetry: the 30-min
    # QUEUE timeout only covers jobs still queued. A page already processing
    # when its worker dies is not covered by it; recovery there is the sweeper's
    # stale rule (PAGE_STALE_SECONDS), so such a page may wait up to
    # visibility_timeout before being redelivered. This is intentional.
    broker_transport_options={
        "priority_steps": list(range(10)),
        "sep": ":",
        "queue_order_strategy": "priority",
        "visibility_timeout": 3600,
    },
)
