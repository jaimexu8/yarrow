"""Unit tests for sweep_stuck_jobs (Phase 2)."""

from datetime import timedelta

from sqlalchemy import update
from yarrow_db.models import Job, JobPage, Page

from app.tasks.ingestion import _utcnow, sweep_stuck_jobs


def _now():
    return _utcnow()


def _mk_job(sessions, document, job, stage, total):
    with sessions() as session:
        job = session.get(Job, job.id)
        job.status = "processing"
        job.current_stage = stage
        job.total_pages = total


def _seed(sessions, seed, page_count, stage):
    document, job = seed(page_count=page_count, status="processing")
    _mk_job(sessions, document, job, stage, page_count)
    with sessions() as session:
        for n in range(1, page_count + 1):
            session.add(
                Page(document_id=document.id, page_number=n, status="pending")
            )
    return document, job


class TestSweep:
    def test_missing_finalize_enqueued(self, sessions, enqueue, redis_stub, seed):
        _document, job = _seed(sessions, seed, 2, "parsing")
        with sessions() as session:
            for n in (1, 2):
                session.add(
                    JobPage(job_id=job.id, page_number=n, status="completed")
                )

        sweep_stuck_jobs()

        assert [
            (name, queue)
            for (name, queue, _) in enqueue
            if name == "app.tasks.ingestion.finalize_document"
        ] == [("app.tasks.ingestion.finalize_document", "default")]

    def test_queue_timeout_fails_queued_page(self, sessions, enqueue, redis_stub, seed):
        _document, job = _seed(sessions, seed, 2, "parsing")
        with sessions() as session:
            session.add(
                JobPage(
                    job_id=job.id,
                    page_number=1,
                    status="queued",
                    queued_at=_now() - timedelta(seconds=2000),
                )
            )
            session.add(JobPage(job_id=job.id, page_number=2, status="completed"))

        sweep_stuck_jobs()

        with sessions() as session:
            page1 = session.query(JobPage).filter(
                JobPage.job_id == job.id, JobPage.page_number == 1
            ).one()
            assert page1.status == "failed"
            assert page1.error_code == "queue_timeout"
        # Failing the last non-terminal page triggers finalize.
        assert any(
            name == "app.tasks.ingestion.finalize_document" for (name, _, _) in enqueue
        )

    def test_queue_timeout_does_not_clobber_completed_page(
        self, sessions, enqueue, redis_stub, seed
    ):
        # Race: a concurrent parse_page._commit_success locked and completed the
        # Page (and its job_page) between this sweep's unlocked read of
        # job_pages and its write. The stale queued job_page must not be failed,
        # and the completed Page must stay completed.
        _document, job = _seed(sessions, seed, 2, "parsing")
        with sessions() as session:
            session.add(
                JobPage(
                    job_id=job.id,
                    page_number=1,
                    status="queued",
                    queued_at=_now() - timedelta(seconds=2000),
                )
            )
            page = (
                session.query(Page)
                .filter(Page.document_id == job.document_id, Page.page_number == 1)
                .one()
            )
            page.status = "completed"

        sweep_stuck_jobs()

        with sessions() as session:
            jp = (
                session.query(JobPage)
                .filter(JobPage.job_id == job.id, JobPage.page_number == 1)
                .one()
            )
            assert not (jp.status == "failed" and jp.error_code == "queue_timeout")
            page = (
                session.query(Page)
                .filter(Page.document_id == job.document_id, Page.page_number == 1)
                .one()
            )
            assert page.status == "completed"

    def test_stale_processing_reenqueues(self, sessions, enqueue, redis_stub, seed):
        _document, job = _seed(sessions, seed, 2, "parsing")
        with sessions() as session:
            job = session.get(Job, job.id)
            job.updated_at = _now() - timedelta(seconds=2000)
            session.add(
                JobPage(job_id=job.id, page_number=1, status="processing")
            )
            session.add(JobPage(job_id=job.id, page_number=2, status="queued"))

        sweep_stuck_jobs()

        assert [
            (queue, args)
            for (name, queue, args) in enqueue
            if name == "app.tasks.ingestion.parse_page"
        ] == [("ocr", [str(job.id), 1])]
        assert redis_stub.store.get(f"swept:{job.id}") is not None

    def test_stuck_ingest_reenqueued(self, sessions, enqueue, redis_stub, seed):
        _document, job = _seed(sessions, seed, 1, "downloading")
        with sessions() as session:
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(updated_at=_now() - timedelta(seconds=16 * 60))
            )

        sweep_stuck_jobs()

        assert [
            (name, queue)
            for (name, queue, _) in enqueue
            if name == "app.tasks.ingestion.ingest_document"
        ] == [("app.tasks.ingestion.ingest_document", "default")]

    def test_rate_limited_job_is_skipped(self, sessions, enqueue, redis_stub, seed):
        _document, job = _seed(sessions, seed, 1, "downloading")
        with sessions() as session:
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(updated_at=_now() - timedelta(seconds=16 * 60))
            )
        # Mark as already swept -> the sweeper must skip it.
        redis_stub.store[f"swept:{job.id}"] = "1"

        sweep_stuck_jobs()

        assert enqueue == []

    def test_no_stuck_jobs_does_nothing(self, sessions, enqueue, redis_stub, seed):
        # A completed job is not a candidate.
        _document, job = seed(page_count=1, status="completed")
        with sessions() as session:
            job = session.get(Job, job.id)
            job.current_stage = "finished"

        sweep_stuck_jobs()

        assert enqueue == []
        assert redis_stub.store == {}
