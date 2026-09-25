import os

from celery import Celery

broker_url = os.getenv("CELERY_BROKER_URL", "redis://valkey:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://valkey:6379/0")

celery_app = Celery(
    "worker", broker=broker_url, backend=result_backend, include=["app.tasks.ingestion"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
)
