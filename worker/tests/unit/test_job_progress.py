import base64
import binascii
import io
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
import requests
from PIL import Image, UnidentifiedImageError
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from yarrow_db.models import Base, Document, Job, JobPage

from app.pipeline.document_parser import DocumentParser, PageError, page_error_code
from app.tasks import ingestion
from app.tasks.ingestion import _record_page_event

OK_RESPONSE = {
    "result": {
        "layoutParsingResults": [
            {"prunedResult": {"width": 40, "height": 40, "parsing_res_list": []}}
        ]
    }
}


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=response)


def _page_image(page_number: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (40 + page_number, 40), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'yarrow.db'}", connect_args={"timeout": 30}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(ingestion, "session_scope", session_scope)
    yield session_scope
    engine.dispose()


def _seed_job(session_scope, page_count: int, status: str = "queued") -> UUID:
    with session_scope() as session:
        document = Document(
            id=uuid4(),
            owner_id=uuid4(),
            filename="scan.pdf",
            file_size_bytes=10,
            file_type="application/pdf",
            storage_key="documents/scan/original",
            status=status,
            page_count=page_count,
        )
        job = Job(
            id=uuid4(),
            document_id=document.id,
            status=status,
            current_stage="queued",
            pages_processed=0,
            total_pages=0,
        )
        session.add_all([document, job])
        return job.id


def _seed_running_job(session_scope, page_count: int) -> UUID:
    job_id = _seed_job(session_scope, page_count, status="processing")
    with session_scope() as session:
        session.add_all(
            JobPage(job_id=job_id, page_number=number, status="queued")
            for number in range(1, page_count + 1)
        )
    return job_id


def _job(session_scope, job_id: UUID) -> Job:
    with session_scope() as session:
        return session.get(Job, job_id)


def _job_pages(session_scope, job_id: UUID) -> dict[int, JobPage]:
    with session_scope() as session:
        rows = session.scalars(select(JobPage).where(JobPage.job_id == job_id))
        return {row.page_number: row for row in rows}


@pytest.fixture
def inference(monkeypatch):
    outcomes: dict[int, object] = {}
    hooks: list = []
    page_count = {"value": 3}

    def load_file(self, file_data):
        self.pages = [
            _page_image(number) for number in range(1, page_count["value"] + 1)
        ]

    def process_page(self, page_data, page_index):
        page_number = self.pages.index(page_data) + 1
        for hook in hooks:
            hook(page_number)
        outcome = outcomes.get(page_number, OK_RESPONSE)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(DocumentParser, "load_file", load_file)
    monkeypatch.setattr(
        DocumentParser, "correct_orientations", lambda self, pages_to_process=None: None
    )
    monkeypatch.setattr(DocumentParser, "process_page", process_page)

    class Inference:
        def configure(self, pages: int, **by_page):
            page_count["value"] = pages
            outcomes.clear()
            outcomes.update(by_page)

        def fail(self, page_number: int, error: BaseException):
            outcomes[page_number] = error

        def on_request(self, hook):
            hooks.append(hook)

    return Inference()


class TestPageErrorCode:
    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (requests.Timeout("slow"), "inference_timeout"),
            (requests.ReadTimeout("slow"), "inference_timeout"),
            (requests.ConnectTimeout("slow"), "inference_timeout"),
            (requests.ConnectionError("refused"), "inference_unavailable"),
            (_http_error(502), "inference_unavailable"),
            (_http_error(503), "inference_unavailable"),
            (_http_error(504), "inference_unavailable"),
            (_http_error(500), "inference_error"),
            (_http_error(422), "inference_error"),
            (requests.HTTPError("no response"), "inference_error"),
            (UnidentifiedImageError("not an image"), "invalid_page_image"),
            (binascii.Error("bad padding"), "invalid_page_image"),
            (ValueError("anything else"), "unknown"),
            (KeyError("result"), "unknown"),
        ],
    )
    def test_exception_maps_to_code(self, error, code):
        assert page_error_code(error) == code

    def test_failed_page_keeps_code_and_raw_message(self, inference):
        inference.configure(pages=2)
        inference.fail(2, requests.Timeout("gateway took 60s"))
        parser = DocumentParser()
        parser.load_file(b"")

        parser.process_sync()

        assert set(parser.page_errors) == {1}
        error = parser.page_errors[1]
        assert error.code == "inference_timeout"
        assert error.message == "Timeout: gateway took 60s"
        assert isinstance(error.raw, requests.Timeout)
        assert parser.failed_page_numbers == [2]


class TestPageEvents:
    def test_each_page_starts_then_finishes_once_under_concurrency(self, inference):
        pages = 4
        inference.configure(pages=pages)
        inference.fail(3, _http_error(503))
        barrier = threading.Barrier(pages, timeout=10)
        inference.on_request(lambda page_number: barrier.wait())

        events: dict[int, list[str]] = defaultdict(list)
        lock = threading.Lock()

        def record(page_number, event):
            with lock:
                events[page_number].append(event)

        parser = DocumentParser(on_page_event=record)
        parser.load_file(b"")
        parser.process_sync()

        assert dict(events) == {
            1: ["started", "completed"],
            2: ["started", "completed"],
            3: ["started", "failed"],
            4: ["started", "completed"],
        }
        assert Counter(event for page in events.values() for event in page) == {
            "started": 4,
            "completed": 3,
            "failed": 1,
        }

    def test_only_the_targeted_pages_report(self, inference):
        inference.configure(pages=4)
        events: list[tuple[int, str]] = []
        parser = DocumentParser(
            on_page_event=lambda number, event: events.append((number, event))
        )
        parser.load_file(b"")

        parser.process_sync((2, 4))

        assert sorted(events) == [
            (2, "completed"),
            (2, "started"),
            (4, "completed"),
            (4, "started"),
        ]

    def test_callback_failure_does_not_fail_the_page(self, inference):
        inference.configure(pages=2)

        def broken(page_number, event):
            raise RuntimeError("database down")

        parser = DocumentParser(on_page_event=broken)
        parser.load_file(b"")
        parser.process_sync()

        assert parser.page_errors == {}
        assert all(parser.parsed_result)


class TestRecordPageEvent:
    def test_started_then_completed(self, sessions):
        job_id = _seed_running_job(sessions, 2)
        parser = DocumentParser()

        _record_page_event(job_id, parser, 1, "started")
        started = _job_pages(sessions, job_id)[1]
        _record_page_event(job_id, parser, 1, "completed")
        finished = _job_pages(sessions, job_id)[1]

        assert started.status == "processing"
        assert started.started_at is not None
        assert started.finished_at is None
        assert finished.status == "completed"
        assert finished.finished_at >= finished.started_at
        assert finished.error_code is None
        assert _job_pages(sessions, job_id)[2].status == "queued"
        assert _job(sessions, job_id).pages_processed == 1

    def test_failed_records_code_and_raw_message(self, sessions):
        job_id = _seed_running_job(sessions, 1)
        parser = DocumentParser()
        parser.page_errors[0] = PageError(
            "inference_unavailable", "ConnectionError: refused"
        )

        _record_page_event(job_id, parser, 1, "started")
        _record_page_event(job_id, parser, 1, "failed")

        page = _job_pages(sessions, job_id)[1]
        assert page.status == "failed"
        assert page.error_code == "inference_unavailable"
        assert page.error_message == "ConnectionError: refused"
        assert _job(sessions, job_id).pages_processed == 1

    def test_repeated_event_is_counted_once(self, sessions):
        job_id = _seed_running_job(sessions, 1)
        parser = DocumentParser()

        _record_page_event(job_id, parser, 1, "started")
        _record_page_event(job_id, parser, 1, "completed")
        _record_page_event(job_id, parser, 1, "completed")

        assert _job(sessions, job_id).pages_processed == 1

    def test_concurrent_increments_are_atomic(self, sessions):
        pages = 24
        job_id = _seed_running_job(sessions, pages)
        parser = DocumentParser()
        barrier = threading.Barrier(pages, timeout=10)

        def run(page_number):
            _record_page_event(job_id, parser, page_number, "started")
            barrier.wait()
            _record_page_event(job_id, parser, page_number, "completed")

        with ThreadPoolExecutor(max_workers=pages) as pool:
            list(pool.map(run, range(1, pages + 1)))

        assert _job(sessions, job_id).pages_processed == pages
        assert {page.status for page in _job_pages(sessions, job_id).values()} == {
            "completed"
        }

    def test_events_after_cancel_leave_pages_canceled(self, sessions):
        job_id = _seed_running_job(sessions, 2)
        parser = DocumentParser()
        _record_page_event(job_id, parser, 1, "started")
        with sessions() as session:
            session.execute(
                update(Job).where(Job.id == job_id).values(status="canceled")
            )
            session.execute(
                update(JobPage)
                .where(
                    JobPage.job_id == job_id,
                    JobPage.status.in_(("queued", "processing")),
                )
                .values(status="canceled")
            )

        _record_page_event(job_id, parser, 1, "completed")
        _record_page_event(job_id, parser, 2, "started")
        _record_page_event(job_id, parser, 2, "failed")

        assert {page.status for page in _job_pages(sessions, job_id).values()} == {
            "canceled"
        }
        assert _job(sessions, job_id).pages_processed == 0
