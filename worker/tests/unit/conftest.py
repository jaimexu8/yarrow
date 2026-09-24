from contextlib import contextmanager
from uuid import uuid4

import pytest
from yarrow_db.models import Document, Job, Page

from app.tasks import ingestion


class FakeSession:
    def __init__(self, store):
        self.store = store
        self.added = []

    def get(self, model, key):
        return self.store.get((model.__name__, key))

    def add_all(self, objects):
        self.added.extend(objects)


class FakeStorage:
    def __init__(self, store: dict[str, bytes]):
        self.store = store

    def download_bytes(self, storage_key) -> bytes:
        return b"fake"


class FakeDocumentParser:
    page_count = 1

    failed = ()
    load_error = None
    inference_error = None

    def __init__(self) -> None:
        self.pages: list[str] = []
        self.page_count = 0
        self.failed = ()
        self.load_error = None
        self.inference_error = None

    @classmethod
    def configure(cls, page_count=1, failed=(), load_error=None, inference_error=None):
        cls.page_count = page_count
        cls.failed = failed
        cls.load_error = load_error
        cls.inference_error = inference_error

    @property
    def failed_page_numbers(self) -> list[int]:
        return sorted(FakeDocumentParser.failed)

    def load_file(self, file_data):
        if FakeDocumentParser.load_error is not None:
            raise FakeDocumentParser.load_error

        self.pages = [f"page_{i}" for i in range(FakeDocumentParser.page_count)]

    def process_sync(self):
        if FakeDocumentParser.inference_error is not None:
            raise FakeDocumentParser.inference_error

    def to_model_objects(self, document, merge_consecutive_tables=False):
        output = []
        for number in range(1, len(self.pages) + 1):
            failed = number in FakeDocumentParser.failed
            output.append(
                Page(
                    id=uuid4(),
                    document_id=document.id,
                    page_number=number,
                    status="failed" if failed else "completed",
                    error_message="inference failed" if failed else None,
                )
            )

        document.page_count = len(self.pages)

        return output


@pytest.fixture
def job(store):
    document = Document(
        id=uuid4(),
        owner_id=uuid4(),
        filename="scan.pdf",
        file_size_bytes=1234,
        file_type="application/pdf",
        storage_key="documents/abc/original",
        status="queued",
        page_count=1,
    )
    job = Job(
        id=uuid4(),
        document_id=document.id,
        status="queued",
        current_stage="queued",
        pages_processed=0,
        total_pages=0,
    )
    job.document = document
    store[("Job", job.id)] = job
    store[("Document", document.id)] = document
    return job


@pytest.fixture
def store():
    return {}


@pytest.fixture
def session(store, monkeypatch):
    fake_session = FakeSession(store)

    @contextmanager
    def session_scope():
        yield fake_session

    monkeypatch.setattr(ingestion, "session_scope", session_scope)
    return fake_session


@pytest.fixture
def storage(store, monkeypatch):
    fake_storage = FakeStorage(store)

    def get_storage():
        return fake_storage

    monkeypatch.setattr(ingestion, "get_storage", get_storage)
    return fake_storage


@pytest.fixture
def parser(monkeypatch):
    parser = FakeDocumentParser()
    monkeypatch.setattr(ingestion, "DocumentParser", FakeDocumentParser)
    return parser
