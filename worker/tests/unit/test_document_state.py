"""Document-level status and error summary test

_documents_state is a pure helper; the pipeline-specific reprocess accounting
moved to the new tasks (test_ingest_document).
"""

from app.tasks.ingestion import _document_state


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
