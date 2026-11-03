"""US-92: redis must honour send_task priority on the worker too."""

from app.celery_app import celery_app


def test_worker_enables_redis_priorities():
    opts = celery_app.conf.broker_transport_options
    assert opts["queue_order_strategy"] == "priority"
    assert list(opts["priority_steps"]) == list(range(10))
