"""Unit tests for ingest_document (Phase 2)."""

import pytest
from sqlalchemy import update
from yarrow_db.models import Job, JobPage, Page, Warning
from yarrow_storage import ObjectNotFoundError

from app.tasks import ingestion
from app.tasks.ingestion import ingest_document


class TestIngest:
    def test_happy_path_seeds_and_enqueues(self, sessions, storage, gateway, enqueue, seed):
        document, job = seed(page_count=3)
        gateway.configure(pages=3)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "processing"
            assert job.current_stage == "parsing"
            assert job.total_pages == 3
            assert job.pages_processed == 0
            assert job.merge_consecutive_tables is False
            assert job.document.page_count == 3
            pages = {
                p.page_number: p
                for p in session.query(Page).filter(Page.document_id == document.id)
            }
            assert [pages[n].status for n in (1, 2, 3)] == ["pending"] * 3
            job_pages = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert all(jp.status == "queued" for jp in job_pages)
            assert all(jp.queued_at is not None for jp in job_pages)

        # Rasters uploaded for the run pages.
        for n in (1, 2, 3):
            assert f"documents/{document.id}/rasters/{n}.png" in storage.store

        # parse_page enqueued for each run page on the ocr queue.
        assert [
            (name, queue)
            for (name, queue, args) in enqueue
            if name == "app.tasks.ingestion.parse_page"
        ] == [("app.tasks.ingestion.parse_page", "ocr")] * 3

    def test_partial_reprocess_scopes_pages(self, sessions, storage, gateway, enqueue, seed):
        document, job = seed(page_count=3)
        gateway.configure(pages=3)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id), pages=(2,))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.total_pages == 1
            pages = {
                p.page_number: p
                for p in session.query(Page).filter(Page.document_id == document.id)
            }
            assert pages[2].status == "pending"
            job_pages = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert [jp.page_number for jp in job_pages] == [2]

        assert [
            (queue, args)
            for (name, queue, args) in enqueue
            if name == "app.tasks.ingestion.parse_page"
        ] == [("ocr", [str(job.id), 2])]

    def test_canceled_job_is_skipped(self, sessions, storage, gateway, enqueue, seed):
        document, job = seed(page_count=2, status="canceled")
        gateway.configure(pages=2)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))

        with sessions() as session:
            assert session.get(Job, job.id).status == "canceled"
            assert session.query(JobPage).filter(JobPage.job_id == job.id).count() == 0
        assert enqueue == []

    def test_unsupported_file_fails_and_raises(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = seed(page_count=1)
        storage.store[document.storage_key] = b"file"

        def broken_load(self, file_data):
            raise ValueError("No loader found for file extension: None")

        monkeypatch.setattr(ingestion.DocumentParser, "load_file", broken_load)

        with pytest.raises(ValueError):
            ingest_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "failed"
            assert job.current_stage == "failed"
            assert "No loader" in job.error_message
            assert (
                session.query(Page).filter(Page.document_id == document.id).count() == 0
            )

    def test_missing_file_marks_failed(self, sessions, storage, gateway, seed):
        _document, job = seed(page_count=1)
        gateway.configure(pages=1)
        # No object in storage -> download raises ObjectNotFoundError.

        with pytest.raises(ObjectNotFoundError):
            ingest_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "failed"
            assert "not found in storage" in job.error_message

    def test_rotated_page_gets_page_image_key(self, sessions, storage, gateway, seed):
        document, job = seed(page_count=2)
        gateway.configure(pages=2, rotations={1: 90.0})
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))

        with sessions() as session:
            p1 = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            p2 = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 2)
                .one()
            )
        assert p1.page_image_key == f"documents/{document.id}/pages/1.png"
        assert p2.page_image_key is None
        assert f"documents/{document.id}/pages/1.png" in storage.store
        assert f"documents/{document.id}/rasters/1.png" in storage.store

    def test_auto_rotated_page_gets_a_rotation_warning(self, sessions, storage, gateway, seed):
        """Ingest records the auto-rotation warning."""
        document, job = seed(page_count=2)
        gateway.configure(pages=2, rotations={1: 90.0})
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))

        with sessions() as session:
            warnings = (
                session.query(Warning)
                .filter(
                    Warning.page_id == (
                        session.query(Page)
                        .filter(Page.document_id == document.id, Page.page_number == 1)
                        .one()
                        .id
                    )
                )
                .all()
            )
        assert [w.message for w in warnings] == ["Page was auto-rotated by 90°"]
        assert all(w.warning_type == "quality" for w in warnings)

    def test_reprocess_clears_stale_counters(
        self, sessions, storage, gateway, enqueue, redis_stub, seed
    ):
        document, job = seed(page_count=2)
        gateway.configure(pages=2)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))
        # A prior delivery count that a reprocess must not inherit.
        redis_stub.store[f"page_deliveries:{job.id}:1"] = 3

        with sessions() as session:
            session.execute(update(Job).where(Job.id == job.id).values(status="queued"))

        ingest_document(str(job.id))

        # The stale counter was cleared; job_pages were reset to queued.
        assert f"page_deliveries:{job.id}:1" not in redis_stub.store
        with sessions() as session:
            job_pages = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert all(jp.status == "queued" for jp in job_pages)
