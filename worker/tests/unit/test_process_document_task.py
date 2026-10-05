"""Unit tests for process_document_task"""

import pytest
from yarrow_storage import ObjectNotFoundError

from app.tasks.ingestion import AllPagesFailedError, process_document_task


# Test parsing successes given supported files
class TestSuccess:
    def test_success(self, job, session, storage, parser):
        parser.configure(page_count=3)

        process_document_task(str(job.id))

        assert job.status == "completed"
        assert job.current_stage == "finished"
        assert job.total_pages == 3
        assert job.pages_processed == 3
        assert job.error_message is None
        assert job.document.status == "completed"
        assert job.document.page_count == 3


# Test behavior for all failures
class TestAllFailure:
    def test_all_failure(self, job, session, storage, parser):
        parser.configure(page_count=3, failed=(1, 2, 3))

        with pytest.raises(AllPagesFailedError):
            process_document_task(str(job.id))

        assert job.status == "failed"
        assert job.current_stage == "parsing"
        assert job.total_pages == 3
        assert job.pages_processed == 0
        assert job.error_message == "3 of 3 page(s) failed: 1, 2, 3"
        assert job.document.status == "failed"
        assert job.document.page_count == 3


# Test behavior of partial failures
class TestPartialFailure:
    def test_partial_failure(self, job, session, storage, parser):
        parser.configure(page_count=3, failed=(1, 2))

        process_document_task(str(job.id))

        assert job.status == "completed"
        assert job.current_stage == "finished"
        assert job.total_pages == 3
        assert job.pages_processed == 1
        assert job.error_message == "2 of 3 page(s) failed: 1, 2"
        assert job.document.status == "completed"
        assert job.document.page_count == 3


# Test missing s3 object behavior


def _delete_job(store, job):
    """What account deletion leaves behind: neither row exists any more"""
    store.pop(("Job", job.id))
    store.pop(("Document", job.document.id))


# Test that deleting a job's rows (account deletion) ends the task quietly.
@pytest.mark.usefixtures("session", "storage", "parser")
class TestJobDeleted:
    def test_deleted_while_queued(self, job, store, session):
        _delete_job(store, job)

        process_document_task(str(job.id))

        assert session.added == []

    def test_deleted_before_download(self, job, store, session, storage, monkeypatch):
        def download_bytes(key):
            _delete_job(store, job)
            raise ObjectNotFoundError(key)

        monkeypatch.setattr(storage, "download_bytes", download_bytes)

        process_document_task(str(job.id))

        assert session.added == []

    def test_deleted_during_inference(self, job, store, session, parser, monkeypatch):
        parser.configure(page_count=3)
        monkeypatch.setattr(type(parser), "process_sync", lambda self, pages=None: _delete_job(store, job))

        process_document_task(str(job.id))

        assert session.added == []

    def test_failure_still_raised_when_job_exists(self, job, parser):
        parser.configure(inference_error=RuntimeError("gateway down"))

        with pytest.raises(RuntimeError):
            process_document_task(str(job.id))

        assert job.status == "failed"
        assert job.error_message == "gateway down"


# A job canceled while queued (US-42) must never be processed, even if its
# Celery message was delivered despite the revoke.
class TestCanceled:
    def test_canceled_job_is_skipped(self, job, session, storage, parser):
        job.status = "canceled"
        job.document.status = "canceled"
        parser.configure(page_count=3)

        process_document_task(str(job.id))

        assert job.status == "canceled"
        assert job.document.status == "canceled"
        assert job.current_stage == "queued"
        assert session.added == []
