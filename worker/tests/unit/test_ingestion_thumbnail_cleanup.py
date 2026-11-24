"""Thumbnail objects must not outlive an aborted prepare_pages run.

Every page number has a deterministic thumbnail key. prepare_pages uploads the
objects before it writes the Page rows, so an abort before that transaction
commits has to remove the objects it just uploaded.
"""

import pytest
from yarrow_db.models import Job, Page

from app.tasks import ingestion


def _preparing_job(sessions, seed, page_count=1):
    document, job = seed(page_count=page_count, status="processing")
    with sessions() as session:
        row = session.get(Job, job.id)
        row.status = "processing"
        row.document.status = "processing"
        row.current_stage = "preparing"
        row.total_pages = page_count
    return document, job


def _key(document_id, page_number):
    return f"documents/{document_id}/thumbnails/{page_number}.webp"


@pytest.fixture(autouse=True)
def thumbnail_bytes(monkeypatch):
    # The gateway fixture's placeholder pages are not real images.
    monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")


def test_successful_run_keeps_thumbnails(
    sessions, storage, gateway, enqueue, redis_stub, seed
):
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"

    ingestion.prepare_pages(str(job.id), [1])

    key = _key(document.id, 1)
    assert storage.store[key] == b"thumb"
    with sessions() as session:
        page = (
            session.query(Page)
            .filter(Page.document_id == document.id, Page.page_number == 1)
            .one()
        )
        assert page.thumbnail_key == key


def test_canceled_job_leaves_no_thumbnail_objects(
    sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
):
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"
    real_store = ingestion._store_thumbnails

    def cancel_after_upload(document_id, parser, page_numbers):
        keys = real_store(document_id, parser, page_numbers)
        assert keys
        assert all(key in storage.store for key in keys.values())
        with sessions() as session:
            row = session.get(Job, job.id)
            row.status = "canceled"
            row.current_stage = "canceled"
        return keys

    monkeypatch.setattr(ingestion, "_store_thumbnails", cancel_after_upload)

    with pytest.raises(ingestion.JobCanceledError):
        ingestion.prepare_pages(str(job.id), [1])

    assert _key(document.id, 1) not in storage.store
    with sessions() as session:
        assert session.query(Page).filter(Page.document_id == document.id).count() == 0


def test_stage_change_after_upload_leaves_no_thumbnail_objects(
    sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
):
    """The status check inside the write transaction is an abort path too."""
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"
    real_store = ingestion._store_thumbnails

    def move_stage_after_upload(document_id, parser, page_numbers):
        keys = real_store(document_id, parser, page_numbers)
        assert keys
        assert all(key in storage.store for key in keys.values())
        with sessions() as session:
            session.get(Job, job.id).current_stage = "parsing"
        return keys

    monkeypatch.setattr(ingestion, "_store_thumbnails", move_stage_after_upload)

    ingestion.prepare_pages(str(job.id), [1])

    assert _key(document.id, 1) not in storage.store
    with sessions() as session:
        assert session.query(Page).filter(Page.document_id == document.id).count() == 0


def test_exception_before_commit_leaves_no_thumbnail_objects(
    sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
):
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"

    def boom(_data):
        assert _key(document.id, 1) in storage.store
        raise RuntimeError("failed before the row write committed")

    monkeypatch.setattr(ingestion, "_raster_size", boom)

    with pytest.raises(RuntimeError, match="failed before the row write committed"):
        ingestion.prepare_pages(str(job.id), [1])

    assert _key(document.id, 1) not in storage.store
    with sessions() as session:
        assert session.query(Page).filter(Page.document_id == document.id).count() == 0


def test_aborted_reprocess_keeps_an_earlier_runs_thumbnail(
    sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
):
    """The same key can still be live from a previous committed run."""
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"
    key = _key(document.id, 1)
    with sessions() as session:
        session.add(
            Page(
                document_id=document.id,
                page_number=1,
                status="completed",
                thumbnail_key=key,
            )
        )

    def boom(_data):
        assert key in storage.store
        raise RuntimeError("reprocess failed before its row write committed")

    monkeypatch.setattr(ingestion, "_raster_size", boom)

    with pytest.raises(RuntimeError):
        ingestion.prepare_pages(str(job.id), [1])

    assert storage.store[key] == b"thumb"


def test_failure_after_commit_keeps_thumbnails(
    sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
):
    """Cleanup must not touch keys once their Page rows are committed."""
    document, job = _preparing_job(sessions, seed)
    gateway.configure(pages=1)
    storage.store[document.storage_key] = b"file"

    def boom(_job_id):
        raise RuntimeError("the gate failed after commit")

    monkeypatch.setattr(ingestion, "_maybe_start_parsing", boom)

    with pytest.raises(RuntimeError, match="the gate failed after commit"):
        ingestion.prepare_pages(str(job.id), [1])

    assert storage.store[_key(document.id, 1)] == b"thumb"
