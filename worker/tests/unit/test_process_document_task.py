"""Unit tests for process_document_task"""

import pytest
from yarrow_db.models.region import RegionImage
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


class TestFigureImages:
    KEY = "documents/abc/figures/region.png"

    def _image_keys(self, session):
        return [obj.image_key for obj in session.added if isinstance(obj, RegionImage)]

    def test_crops_are_uploaded(self, job, store, session, storage, parser):
        """Test whether cropped images are uploaded to storage"""
        parser.configure(figures={self.KEY: b"png"})

        process_document_task(str(job.id))

        assert store[self.KEY] == b"png"
        assert self._image_keys(session) == [self.KEY]

    def test_failed_upload_drops_only_the_picture(self, job, session, storage, parser, monkeypatch):
        """Test that a failed upload only drops the picture"""
        parser.configure(figures={self.KEY: b"png"})

        def upload_file(file, key):
            raise RuntimeError("storage down")

        monkeypatch.setattr(storage, "upload_file", upload_file)

        process_document_task(str(job.id))

        assert job.status == "completed"
        assert self._image_keys(session) == []

    def test_uncommitted_crops_are_removed(self, job, store, session, storage, parser, monkeypatch):
        """Test if image is removed when the database fails before commit"""
        parser.configure(figures={self.KEY: b"png"})

        # Fails after the crops are uploaded, before anything is committed
        def add_all(objects):
            raise RuntimeError("database down")

        monkeypatch.setattr(session, "add_all", add_all)

        with pytest.raises(RuntimeError):
            process_document_task(str(job.id))

        assert self.KEY not in store

    def test_replaced_crops_are_removed_after_commit(self, job, store, session, storage, parser):
        """Test if replaced crops are removed after commit"""
        
        old_key = "documents/abc/figures/old.png"
        store[old_key] = b"old"
        session.existing_image_keys = [old_key]
        parser.configure(figures={self.KEY: b"png"})

        process_document_task(str(job.id))

        assert old_key not in store
        assert store[self.KEY] == b"png"

    def test_replaced_crops_are_kept_when_nothing_is_committed(self, job, store, session, storage, parser, monkeypatch):
        """Test if replaced crops are kept when nothing is committed"""
        
        old_key = "documents/abc/figures/old.png"
        store[old_key] = b"old"
        session.existing_image_keys = [old_key]

        # Fails after the crops are uploaded, before anything is committed
        def add_all(objects):
            raise RuntimeError("database down")

        monkeypatch.setattr(session, "add_all", add_all)

        with pytest.raises(RuntimeError):
            process_document_task(str(job.id))

        assert store[old_key] == b"old"
