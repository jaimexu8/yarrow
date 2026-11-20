"""OCR operator metrics for GET /admin/ocr/metrics."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, JobPage, User

from app.services.ocr_metrics import (
    PRIORITY_SEP,
    _queue_depth,
    collect_ocr_metrics,
)


class FakeRedis:
    """Stands in for the broker client: llen returns a dict lookup."""

    def __init__(self, lengths: dict[str, int]) -> None:
        self.lengths = lengths
        self.asked: list[str] = []

    def llen(self, key: str) -> int:
        self.asked.append(key)
        return self.lengths.get(key, 0)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TestQueueDepth:
    def test_sums_bare_list_and_higher_priorities(self):
        # kombu names the priority-0 list as the bare queue name, so the full
        # set is "ocr" + "ocr:1" .. "ocr:9".
        lengths = {"ocr": 1}
        lengths.update(
            {
                f"ocr{PRIORITY_SEP}{priority}": (priority + 1) % 3
                for priority in range(1, 10)
            }
        )
        fake = FakeRedis(lengths)

        assert _queue_depth(fake) == sum(lengths.values())
        assert fake.asked == ["ocr"] + [
            f"ocr{PRIORITY_SEP}{priority}" for priority in range(1, 10)
        ]

    def test_unknown_lists_count_as_zero(self):
        fake = FakeRedis({"ocr:3": 4})

        assert _queue_depth(fake) == 4

    def test_priority_zero_messages_live_in_the_bare_list(self):
        # Messages sent without an explicit priority (as parse_page are) land
        # in the bare "ocr" list; there is no "ocr:0" list.
        fake = FakeRedis({"ocr": 5, "ocr:0": 99})

        assert _queue_depth(fake) == 5
        assert "ocr:0" not in fake.asked

    def test_other_queues_are_not_summed(self):
        fake = FakeRedis({"celery": 9, "ocr:9": 2})

        assert _queue_depth(fake, "default") == 0
        assert _queue_depth(fake) == 2


@pytest.fixture
async def seeded(db_session: AsyncSession):
    """Two active jobs and one terminal job, with pages in each state.

    - job A (processing): page 1 queued 90 s ago, page 2 processing,
      page 3 failed with queue_timeout 2 min ago (inside the window).
    - job B (queued): page 1 queued 300 s ago (the oldest).
    - job C (failed, terminal): page 1 failed with queue_timeout 10 min ago
      (outside the window), page 2 failed with inference_error 1 min ago
      (not a rejected code).
    """
    now = _now()
    user = User(
        email=f"metrics-{uuid.uuid4().hex[:12]}@example.com", hashed_password="x"
    )
    db_session.add(user)
    await db_session.flush()

    def make_document(filename: str) -> Document:
        document = Document(
            id=uuid.uuid4(),
            owner_id=user.id,
            filename=filename,
            file_size_bytes=1,
            file_type="application/pdf",
            storage_key=f"documents/{uuid.uuid4()}/original.pdf",
            status="processing",
        )
        db_session.add(document)
        return document

    def make_job(document: Document, status: str) -> Job:
        job = Job(
            id=uuid.uuid4(),
            document_id=document.id,
            status=status,
            current_stage="parsing",
        )
        db_session.add(job)
        return job

    def add_page(job: Job, page_number: int, status: str, **kwargs) -> JobPage:
        page = JobPage(job_id=job.id, page_number=page_number, status=status, **kwargs)
        db_session.add(page)
        return page

    job_a = make_job(make_document("a.pdf"), "processing")
    add_page(job_a, 1, "queued", queued_at=now - timedelta(seconds=90))
    add_page(
        job_a, 2, "processing", queued_at=now - timedelta(seconds=80), started_at=now
    )
    page_a3 = add_page(
        job_a,
        3,
        "failed",
        error_code="queue_timeout",
        finished_at=now - timedelta(minutes=2),
    )

    job_b = make_job(make_document("b.pdf"), "queued")
    add_page(job_b, 1, "queued", queued_at=now - timedelta(seconds=300))

    job_c = make_job(make_document("c.pdf"), "failed")
    add_page(
        job_c,
        1,
        "failed",
        error_code="queue_timeout",
        finished_at=now - timedelta(minutes=10),
    )
    add_page(
        job_c,
        2,
        "failed",
        error_code="inference_error",
        finished_at=now - timedelta(minutes=1),
    )
    await db_session.flush()

    return {
        "now": now,
        "job_a": job_a,
        "job_b": job_b,
        "job_c": job_c,
        "page_a3": page_a3,
    }


class TestCollectOcrMetrics:
    async def test_counts_and_ages(self, db_session, seeded):
        metrics = await collect_ocr_metrics(db_session, queue_depth=17)

        assert metrics.queue_depth == 17
        assert metrics.pages_queued == 2
        assert metrics.pages_processing == 1
        assert metrics.oldest_queued_seconds is not None
        assert metrics.oldest_queued_seconds == pytest.approx(300, abs=5)
        assert metrics.rejected_last_5m == 1

    async def test_terminal_job_pages_do_not_count_as_queued(self, db_session, seeded):
        metrics = await collect_ocr_metrics(db_session, queue_depth=0)

        # job C is terminal: its pages never enter the queued/processing counts
        # and its 10-minute-old queue_timeout sits outside the 5-minute window
        assert metrics.pages_queued == 2
        assert metrics.pages_processing == 1
        assert metrics.rejected_last_5m == 1

    async def test_no_queued_pages_gives_null_oldest(self, db_session, seeded):
        for job in (seeded["job_a"], seeded["job_b"]):
            for page in (
                await db_session.scalars(
                    select(JobPage).where(
                        JobPage.job_id == job.id, JobPage.status == "queued"
                    )
                )
            ).all():
                page.status = "processing"
        await db_session.flush()

        metrics = await collect_ocr_metrics(db_session, queue_depth=3)

        assert metrics.pages_queued == 0
        assert metrics.oldest_queued_seconds is None
        assert metrics.pages_processing == 3

    async def test_rejection_window_is_five_minutes(self, db_session, seeded):
        page = seeded["page_a3"]
        page.finished_at = seeded["now"] - timedelta(minutes=6)
        await db_session.flush()

        metrics = await collect_ocr_metrics(db_session, queue_depth=0)

        assert metrics.rejected_last_5m == 0
