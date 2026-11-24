"""Unit tests for the batched ingest_document.

ingest_document now counts pages, stages the job as "preparing" and fans out
prepare_pages batches on the ocr queue. Row/thumbnail/raster seeding moved into
prepare_pages, which these tests leave to its own module.
"""

import pytest
from yarrow_db.models import Job, JobPage, Page
from yarrow_storage import ObjectNotFoundError

from app.pipeline.document_parser import DocumentParser
from app.tasks import ingestion
from app.tasks.ingestion import _utcnow, ingest_document

PREPARE = "app.tasks.ingestion.prepare_pages"
PARSE = "app.tasks.ingestion.parse_page"


def _pin_batch_size(monkeypatch, size):
    """Pin the new batch-size setting without depending on it existing yet.

    The instance form is the one to use once the setting is declared; at this
    commit pydantic rejects the unknown field, so write the instance __dict__
    instead (which also overrides a declared field later).
    """
    try:
        monkeypatch.setattr(
            ingestion.settings, "INGEST_PAGE_BATCH_SIZE", size, raising=False
        )
    except ValueError:
        monkeypatch.setattr(
            ingestion.settings,
            "__dict__",
            {**vars(ingestion.settings), "INGEST_PAGE_BATCH_SIZE": size},
        )


def _batches(enqueue):
    """The page-number lists of the prepare_pages tasks that were sent."""
    return [list(args[1]) for name, _queue, args in enqueue if name == PREPARE]


class TestIngest:
    def test_happy_path_batches_and_stages_preparing(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        document, job = seed(page_count=6)
        gateway.configure(pages=6)
        storage.store[document.storage_key] = b"file"
        # A stale row from an older run must be removed before batching.
        with sessions() as session:
            session.add(
                JobPage(
                    job_id=job.id,
                    page_number=99,
                    status="completed",
                    queued_at=_utcnow(),
                )
            )

        ingest_document(str(job.id), merge_consecutive_tables=True)

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "processing"
            assert job.current_stage == "preparing"
            assert job.total_pages == 6
            assert job.pages_processed == 0
            assert job.merge_consecutive_tables is True
            assert job.document.page_count == 6
            # ingest no longer seeds rows or rasters; prepare_pages does that.
            assert session.query(JobPage).filter(JobPage.job_id == job.id).count() == 0
            assert (
                session.query(Page).filter(Page.document_id == document.id).count() == 0
            )

        assert _batches(enqueue) == [[1, 2], [3, 4], [5, 6]]
        assert [(name, queue) for name, queue, _args in enqueue if name == PREPARE] == [
            (PREPARE, "ocr")
        ] * 3
        assert [args[0] for _name, _queue, args in enqueue] == [str(job.id)] * 3
        assert not any(name == PARSE for name, _queue, _args in enqueue)
        assert not any(
            key.startswith(f"documents/{document.id}/rasters/") for key in storage.store
        )

    def test_incomplete_last_batch_is_allowed(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 25)
        document, job = seed(page_count=28)
        gateway.configure(pages=28)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id))

        batches = _batches(enqueue)
        assert [len(batch) for batch in batches] == [25, 3]
        assert batches == [list(range(1, 26)), [26, 27, 28]]

    def test_empty_run_goes_to_parsing_without_enqueues(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        document, job = seed(page_count=3)
        gateway.configure(pages=3)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id), pages=[])

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.current_stage == "parsing"
            assert job.total_pages == 0
        assert enqueue == []

    def test_partial_reprocess_batches_only_requested_pages(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 1)
        document, job = seed(page_count=6)
        gateway.configure(pages=6)
        storage.store[document.storage_key] = b"file"

        ingest_document(str(job.id), pages=(2, 5))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.current_stage == "preparing"
            assert job.total_pages == 2
            assert job.document.page_count == 6
        assert _batches(enqueue) == [[2], [5]]
        # Non-run pages are never batched, so nothing seeds their rows/rasters.
        with sessions() as session:
            assert (
                session.query(Page).filter(Page.document_id == document.id).count() == 0
            )
        assert not any(
            key.startswith(f"documents/{document.id}/rasters/") for key in storage.store
        )

    def test_canceled_job_is_skipped(self, sessions, storage, gateway, enqueue, seed):
        _document, job = seed(page_count=2, status="canceled")
        gateway.configure(pages=2)
        storage.store[_document.storage_key] = b"file"

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

        def broken_count(self, file_data, **kwargs):
            raise ValueError("No loader found for file extension: None")

        monkeypatch.setattr(DocumentParser, "count_pages", broken_count)

        with pytest.raises(ValueError):
            ingest_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.status == "failed"
            assert job.current_stage == "failed"
            assert job.error_stage == "downloading"
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
            assert job.error_stage == "downloading"
            assert "not found in storage" in job.error_message

    def test_short_circuit_enqueues_only_missing_batches(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        document, job = seed(page_count=4)
        gateway.configure(pages=4)
        storage.store[document.storage_key] = b"file"
        queued_at = _utcnow()
        with sessions() as session:
            job = session.get(Job, job.id)
            job.status = "processing"
            job.current_stage = "preparing"
            job.total_pages = 4
            job.document.status = "processing"
            session.add_all(
                [
                    JobPage(
                        job_id=job.id,
                        page_number=number,
                        status="queued",
                        queued_at=queued_at,
                    )
                    for number in (1, 2)
                ]
            )
            session.add(Page(document_id=document.id, page_number=1, status="pending"))
        with sessions() as session:
            before = {
                row.page_number: (row.id, row.queued_at)
                for row in session.query(JobPage).filter(JobPage.job_id == job.id)
            }

        ingest_document(str(job.id))

        with sessions() as session:
            job = session.get(Job, job.id)
            # The preparing job was not reset or re-staged.
            assert job.current_stage == "preparing"
            assert job.total_pages == 4
            after = {
                row.page_number: (row.id, row.queued_at)
                for row in session.query(JobPage).filter(JobPage.job_id == job.id)
            }
        assert after == before
        assert _batches(enqueue) == [[3, 4]]
        assert not any(name == PARSE for name, _queue, _args in enqueue)
