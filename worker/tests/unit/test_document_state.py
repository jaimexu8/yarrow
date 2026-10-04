"""Document-level status and error summary test"""

import pytest

from app.tasks.ingestion import (
    AllPagesFailedError,
    _document_state,
    process_document_task,
)


class TestDocumentState:
    def test_all_pages_parsed(self):
        rows = [(1, "completed"), (2, "completed")]
        assert _document_state(rows, 2) == ("completed", None)

    def test_partial_failure_is_still_completed(self):
        """A document with one bad page is worth viewing and exporting"""
        rows = [(1, "completed"), (2, "failed"), (3, "completed")]
        status, message = _document_state(rows, 3)
        assert status == "completed"
        assert message == "1 of 3 page(s) failed: 2"

    def test_every_page_failed(self):
        rows = [(1, "failed"), (2, "failed")]
        assert _document_state(rows, 2) == (
            "failed",
            "2 of 2 page(s) failed: 1, 2",
        )

    def test_no_pages_at_all(self):
        assert _document_state([], 0) == ("failed", None)

    def test_summary_survives_a_reprocess_that_touched_other_pages(self):
        """verifies that document status is marked completed while still reporting failures on other pages after a reprocess."""
        rows = [(1, "completed"), (2, "completed"), (3, "failed"), (4, "failed")]
        status, message = _document_state(rows, 4)
        assert status == "completed"
        assert message == "2 of 4 page(s) failed: 3, 4"

    def test_failed_page_numbers_are_ordered(self):
        rows = [(3, "failed"), (1, "failed"), (2, "completed")]
        assert _document_state(rows, 3)[1] == "2 of 3 page(s) failed: 1, 3"


class TestPartialReprocessAccounting:
    def test_job_status_is_per_run_but_progress_is_per_document(self, session, storage, parser, job):
        """Reprocessing page 2 alone and failing is a failed job.

        pages_processed stays document-level so it remains comparable with
        total_pages, while status and error_message describe only this run.
        """
        parser.configure(page_count=3, failed=(2,))

        with pytest.raises(AllPagesFailedError):
            process_document_task(str(job.id), page_to_process=(2,))

        assert job.status == "failed"
        assert job.error_message == "1 of 1 page(s) failed: 2"
        # Pages 1 and 3 were parsed earlier and are untouched by this run.
        assert job.pages_processed == 2

        # The other two pages were never touched and still hold content.
        assert job.document.status == "completed"
        assert job.document.error_message == "1 of 3 page(s) failed: 2"

    def test_reprocessing_no_pages_is_a_no_op(self, session, storage, parser, job):
        """A single-page document has no failed pages, so the retry list is empty.

        Attempting nothing must not be reported as a run in which everything
        failed.
        """
        parser.configure(page_count=1, failed=())

        process_document_task(str(job.id), page_to_process=())

        assert job.status == "completed"
        assert job.error_message is None
        assert job.pages_processed == 1
