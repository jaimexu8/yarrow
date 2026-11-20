"""Unit-test fixtures.

The Phase Celery tasks are tested against a real SQLite database
(SQLite 3.46+ supports the FOR UPDATE row locks and the RETURNING clause the
tasks use), a stubbed OCR gateway (the parser's per-page entry point is
replaced so no network or live cluster is needed), an in-memory object
store, and an in-memory Valkey DB 3 client for the delivery counters and
sweep markers.
"""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from yarrow_db.models import Base, Document, Job, Page, Warning
from yarrow_db.models.region import RegionImage
from yarrow_storage import ObjectNotFoundError

from app.pipeline.document_parser import ModelInfo, PageError, page_error_code
from app.tasks import ingestion


class FakeStorage:
    """An in-memory object store; download_bytes raises when the key is
    missing, like the real provider."""

    def __init__(self, store: dict[str, bytes]):
        self.store = store

    def download_bytes(self, storage_key):
        if storage_key in self.store:
            return self.store[storage_key]
        raise ObjectNotFoundError(storage_key)

    def upload_file(self, file, key):
        self.store[key] = file.read()

    def delete_file(self, key):
        self.store.pop(key, None)


class FakeRedis:
    """In-memory stand-in for the Valkey DB 3 counter/sweep store."""

    def __init__(self, store: dict, down: bool = False):
        self.store = store
        self.down = down

    def _fail(self):
        if self.down:
            raise ConnectionError("valkey unreachable")

    def incr(self, key):
        self._fail()
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = value
        return value

    def expire(self, key, ttl):
        self._fail()

    def delete(self, key):
        self._fail()
        self.store.pop(key, None)

    def get(self, key):
        self._fail()
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self._fail()
        self.store[key] = value


class Gateway:
    """State + control for the stubbed OCR gateway.

    The conftest `gateway` fixture turns these into plain functions (parser as
    the first argument, so they bind correctly when called on a DocumentParser
    instance) and patches them onto DocumentParser via monkeypatch.

    - load_file produces page_count placeholder pages (valid base64, not a real
      image, so _raster_size / build_thumbnail fail gracefully).
    - correct_orientations records a rotation for each page in rotations.
    - parse_single_page returns a completed Page (with optional corrected image
      and figure crops), or records a page_error and returns a failed Page when
      fail(page_no, error) was set.
    """

    def __init__(self):
        self.page_count = 1
        self.failures: dict[int, BaseException] = {}
        self.rotations: dict[int, float] = {}
        self.model = ModelInfo("M1", "1.0")
        self.calls: list[int] = []
        self.figures: dict[str, bytes] = {}
        self.manual: dict[int, int] = {}
        self.warnings: dict[int, str] = {}

    def configure(
        self, pages=1, model=None, rotations=None, figures=None, manual=None, warnings=None
    ):
        self.page_count = pages
        if model is not None:
            self.model = model
        self.rotations = dict(rotations or {})
        self.figures = dict(figures or {})
        self.manual = dict(manual or {})
        self.warnings = dict(warnings or {})

    def fail(self, page_no, error):
        self.failures[page_no] = error

    def load_file(self, parser, file_data):
        import base64

        parser.pages = [
            base64.b64encode(f"page_{i}".encode()).decode()
            for i in range(self.page_count)
        ]

    def correct_orientations(self, parser, pages_to_process=None):
        for number, angle in self.rotations.items():
            parser.page_orientations[number - 1] = angle

    def apply_manual_rotations(self, parser, rotations):
        parser.manual_rotations.update(rotations)

    def parse_single_page(self, parser, document, page_index):
        number = page_index + 1
        self.calls.append(number)
        error = self.failures.get(number)
        if error is not None:
            parser.page_errors[page_index] = PageError(
                page_error_code(error),
                f"{type(error).__name__}: {error}",
                error,
            )
            return [
                Page(document_id=document.id, page_number=number, status="failed")
            ]
        page = Page(
            document_id=document.id,
            page_number=number,
            width=40.0,
            height=40.0,
            status="completed",
            model_name=self.model.name,
            model_version=self.model.version,
        )
        if page_index in self.rotations:
            key = f"documents/{document.id}/pages/{number}.png"
            page.page_image_key = key
            parser.page_images[key] = b"corrected"
        objects = [page]
        if page_index in self.warnings:
            objects.append(
                Warning(page_id=page.id, warning_type="quality", message=self.warnings[page_index])
            )
        if self.figures:
            parser.figure_images.update(self.figures)
            objects.extend(
                RegionImage(id=uuid4(), region_id=uuid4(), image_key=key)
                for key in self.figures
            )
        return objects


@pytest.fixture
def seed(sessions):
    """Create a document + job row; the returned callable takes (page_count,
    status) and returns (document, job)."""

    def _seed(page_count=1, status="queued"):
        with sessions() as session:
            document = Document(
                id=uuid4(),
                owner_id=uuid4(),
                filename="scan.pdf",
                file_size_bytes=1234,
                file_type="application/pdf",
                storage_key="documents/abc/original",
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
        return document, job

    return _seed


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """A real SQLite database (shared in-memory connection) so the tasks' raw
    SQL and row locks behave as in Postgres."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


@pytest.fixture
def store():
    return {}


@pytest.fixture
def storage(store, monkeypatch):
    fake = FakeStorage(store)
    monkeypatch.setattr(ingestion, "get_storage", lambda: fake)
    return fake


@pytest.fixture
def redis_stub(monkeypatch):
    """Valkey DB 3, in memory. Set .down to simulate an unreachable Valkey
    (the tasks must then skip the counters and continue)."""
    store: dict = {}
    fake = FakeRedis(store, down=False)
    monkeypatch.setattr(ingestion, "_page_counters_client", lambda: fake)
    return fake


@pytest.fixture
def gateway(monkeypatch):
    from app.pipeline.document_parser import DocumentParser

    gw = Gateway()

    def load_file(parser, file_data):
        gw.load_file(parser, file_data)

    def correct_orientations(parser, pages_to_process=None):
        gw.correct_orientations(parser, pages_to_process)

    def apply_manual_rotations(parser, rotations):
        gw.apply_manual_rotations(parser, rotations)

    def parse_single_page(parser, document, page_index):
        return gw.parse_single_page(parser, document, page_index)

    monkeypatch.setattr(DocumentParser, "load_file", load_file)
    monkeypatch.setattr(DocumentParser, "correct_orientations", correct_orientations)
    monkeypatch.setattr(DocumentParser, "apply_manual_rotations", apply_manual_rotations)
    monkeypatch.setattr(DocumentParser, "parse_single_page", parse_single_page)
    return gw


@pytest.fixture
def enqueue(monkeypatch):
    """Record every task the tasks enqueue instead of sending to the broker."""
    sent: list[tuple[str, str, list]] = []

    def send_task(name, args=None, queue=None, **kwargs):
        sent.append((name, queue or "default", list(args or [])))

    monkeypatch.setattr(ingestion.celery_app, "send_task", send_task)
    return sent
