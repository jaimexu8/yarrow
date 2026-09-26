"""Unit tests for process_document_task"""

import pytest

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
