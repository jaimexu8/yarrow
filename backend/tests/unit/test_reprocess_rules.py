from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.services.reprocess import (
    interrupted,
    is_interrupted,
    is_running,
    pages_to_process,
    reprocess_scope,
)

# Naive UTC, like the timestamps the models write.
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
STALE = NOW - timedelta(seconds=settings.JOB_INTERRUPTED_AFTER_SECONDS + 1)
FRESH = NOW - timedelta(seconds=settings.JOB_INTERRUPTED_AFTER_SECONDS - 1)


def job(status, updated_at=FRESH):
    return {"status": status, "updated_at": updated_at}


class TestIsInterrupted:
    @pytest.mark.parametrize("status", ["queued", "processing"])
    def test_silent_past_the_time_limit(self, status):
        assert is_interrupted(job(status, STALE), NOW)

    @pytest.mark.parametrize("status", ["queued", "processing"])
    def test_recently_updated_is_still_running(self, status):
        assert not is_interrupted(job(status), NOW)
        assert is_running(job(status), NOW)

    @pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
    def test_finished_jobs_are_never_interrupted(self, status):
        assert not is_interrupted(job(status, STALE), NOW)
        assert not is_running(job(status), NOW)

    def test_active_job_without_a_timestamp_counts_as_lost(self):
        assert is_interrupted(job("processing", None), NOW)

    def test_no_job(self):
        assert not is_interrupted(None, NOW)
        assert not is_running(None, NOW)

    def test_in_progress_document_with_no_active_job(self):
        assert interrupted("processing", job("failed"), NOW)
        assert interrupted("queued", None, NOW)


class TestReprocessScope:
    def test_running_job_cannot_be_reprocessed(self):
        assert reprocess_scope("processing", 10, 4, job("processing"), NOW) is None

    def test_interrupted_run_resumes_its_unfinished_pages(self):
        scope = reprocess_scope("processing", 10, 4, job("processing", STALE), NOW)
        assert scope == "incomplete"

    def test_failed_pages_are_reprocessed(self):
        assert (
            reprocess_scope("completed", 10, 8, job("completed"), NOW) == "incomplete"
        )

    def test_failed_document_without_a_page_count(self):
        assert reprocess_scope("failed", None, 0, job("failed"), NOW) == "incomplete"

    def test_fully_processed_document_is_reprocessed_in_full(self):
        assert reprocess_scope("completed", 10, 10, job("completed"), NOW) == "all"

    def test_canceled_document_is_processed_in_full(self):
        assert reprocess_scope("canceled", None, 0, job("canceled"), NOW) == "all"


class TestPagesToProcess:
    def test_every_page_not_completed(self):
        assert pages_to_process(6, [1, 2, 4, 6]) == [3, 5]

    def test_all_pages_when_none_completed(self):
        assert pages_to_process(3, []) == [1, 2, 3]

    def test_unknown_page_count_means_the_whole_document(self):
        assert pages_to_process(None, []) is None

    def test_nothing_left(self):
        assert pages_to_process(2, [1, 2]) == []
