import base64
import io
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
import requests
from PIL import Image
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from yarrow_db.models import Base, Document, Job, Page

from app.core.config import settings
from app.pipeline.document_parser import DocumentParser
from app.pipeline.thumbnails import build_thumbnail
from app.tasks import ingestion
from app.tasks.ingestion import process_document_task

OK_RESPONSE = {
    "result": {
        "layoutParsingResults": [
            {"prunedResult": {"width": 40, "height": 40, "parsing_res_list": []}}
        ]
    }
}


def _encode(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _page_image(page_number: int) -> str:
    return _encode(Image.new("RGB", (800, 1000 + page_number), "white"))


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'yarrow.db'}")
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


class Storage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.failing: set[str] = set()
        self.uploaded_before_ocr: set[str] = set()

    def download_bytes(self, key):
        return b"file"

    def upload_file(self, file, key):
        if key in self.failing:
            raise ConnectionError("storage unavailable")
        self.objects[key] = file.read()

    def delete_file(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def storage(monkeypatch):
    storage = Storage()
    monkeypatch.setattr(ingestion, "get_storage", lambda: storage)
    return storage


@pytest.fixture
def pages(monkeypatch, storage):
    page_count = {"value": 3}
    hooks: list = []

    def load_file(self, file_data):
        self.pages = [_page_image(number) for number in range(1, page_count["value"] + 1)]

    def process_page(self, page_data, page_index):
        storage.uploaded_before_ocr.update(storage.objects)
        page_number = self.pages.index(page_data) + 1
        for hook in hooks:
            hook(page_number)
        return OK_RESPONSE

    monkeypatch.setattr(DocumentParser, "load_file", load_file)
    monkeypatch.setattr(DocumentParser, "correct_orientations", lambda self: None)
    monkeypatch.setattr(DocumentParser, "process_page", process_page)

    class Pages:
        def configure(self, count: int):
            page_count["value"] = count

        def on_request(self, hook):
            hooks.append(hook)

    return Pages()


def _seed_job(session_scope, page_count: int) -> tuple[UUID, UUID]:
    with session_scope() as session:
        document = Document(
            id=uuid4(),
            owner_id=uuid4(),
            filename="scan.pdf",
            file_size_bytes=10,
            file_type="application/pdf",
            storage_key="documents/scan/original",
            status="queued",
            page_count=page_count,
        )
        job = Job(
            id=uuid4(),
            document_id=document.id,
            status="queued",
            current_stage="queued",
            pages_processed=0,
            total_pages=0,
        )
        session.add_all([document, job])
        return document.id, job.id


def _pages(session_scope, document_id: UUID) -> dict[int, Page]:
    with session_scope() as session:
        rows = session.scalars(select(Page).where(Page.document_id == document_id))
        return {row.page_number: row for row in rows}


def _key(document_id: UUID, page_number: int) -> str:
    return f"documents/{document_id}/thumbnails/{page_number}.webp"


class TestBuildThumbnail:
    def test_is_160_px_wide_webp_keeping_aspect_ratio(self):
        thumbnail = Image.open(io.BytesIO(build_thumbnail(_encode(Image.new("RGB", (1700, 2200), "white")))))

        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (160, 207)

    def test_landscape_page_keeps_its_ratio(self):
        thumbnail = Image.open(io.BytesIO(build_thumbnail(_encode(Image.new("RGB", (2000, 1000), "white")))))

        assert thumbnail.size == (160, 80)

    @pytest.mark.parametrize("mode", ["L", "P", "RGBA", "CMYK", "1"])
    def test_any_image_mode_is_converted(self, mode):
        image = Image.new(mode, (320, 400))
        buffer = io.BytesIO()
        image.save(buffer, format="TIFF")
        page_b64 = base64.b64encode(buffer.getvalue()).decode()

        thumbnail = Image.open(io.BytesIO(build_thumbnail(page_b64)))

        assert thumbnail.size == (160, 200)

    def test_follows_the_worker_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "THUMBNAIL_WIDTH_PX", 80)

        thumbnail = Image.open(io.BytesIO(build_thumbnail(_encode(Image.new("RGB", (800, 1000), "white")))))

        assert thumbnail.size == (80, 100)


class TestThumbnailGeneration:
    def test_every_page_gets_a_thumbnail_before_ocr(self, sessions, storage, pages):
        pages.configure(3)
        document_id, job_id = _seed_job(sessions, 3)

        process_document_task(str(job_id))

        stored = _pages(sessions, document_id)
        assert {number: page.thumbnail_key for number, page in stored.items()} == {
            number: _key(document_id, number) for number in (1, 2, 3)
        }
        assert all(page.has_thumbnail for page in stored.values())
        assert {_key(document_id, number) for number in (1, 2, 3)} <= storage.uploaded_before_ocr
        thumbnail = Image.open(io.BytesIO(storage.objects[_key(document_id, 1)]))
        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (160, 200)

    def test_upload_failure_does_not_fail_the_job(self, sessions, storage, pages):
        pages.configure(2)
        document_id, job_id = _seed_job(sessions, 2)
        storage.failing.add(_key(document_id, 2))

        process_document_task(str(job_id))

        with sessions() as session:
            job = session.get(Job, job_id)
            document = session.get(Document, document_id)
        stored = _pages(sessions, document_id)
        assert job.status == "completed"
        assert document.status == "completed"
        assert {number: page.status for number, page in stored.items()} == {1: "completed", 2: "completed"}
        assert stored[1].thumbnail_key == _key(document_id, 1)
        assert stored[2].thumbnail_key is None

    def test_unreadable_page_image_does_not_fail_the_job(self, sessions, storage, pages, monkeypatch):
        pages.configure(1)
        document_id, job_id = _seed_job(sessions, 1)

        def broken(page_b64):
            raise OSError("cannot identify image file")

        monkeypatch.setattr(ingestion, "build_thumbnail", broken)

        process_document_task(str(job_id))

        with sessions() as session:
            assert session.get(Job, job_id).status == "completed"
        assert _pages(sessions, document_id)[1].thumbnail_key is None

    def test_reprocess_rewrites_only_its_pages(self, sessions, storage, pages):
        pages.configure(3)
        document_id, job_id = _seed_job(sessions, 3)
        process_document_task(str(job_id))
        storage.objects = {key: b"old" for key in storage.objects}
        with sessions() as session:
            rerun = Job(id=uuid4(), document_id=document_id, status="queued", current_stage="queued")
            session.add(rerun)

        process_document_task(str(rerun.id), page_to_process=(2,))

        assert storage.objects[_key(document_id, 1)] == b"old"
        assert storage.objects[_key(document_id, 3)] == b"old"
        assert storage.objects[_key(document_id, 2)] != b"old"
        assert all(page.thumbnail_key for page in _pages(sessions, document_id).values())

    def test_canceled_first_run_leaves_no_thumbnails(self, sessions, storage, pages):
        pages.configure(1)
        document_id, job_id = _seed_job(sessions, 1)

        def cancel(page_number):
            with sessions() as session:
                session.execute(update(Job).where(Job.id == job_id).values(status="canceled"))

        pages.on_request(cancel)

        process_document_task(str(job_id))

        assert _key(document_id, 1) not in storage.objects

    def test_canceled_reprocess_keeps_the_thumbnails_pages_point_at(self, sessions, storage, pages):
        pages.configure(1)
        document_id, job_id = _seed_job(sessions, 1)
        process_document_task(str(job_id))
        with sessions() as session:
            rerun = Job(id=uuid4(), document_id=document_id, status="queued", current_stage="queued")
            session.add(rerun)

        def cancel(page_number):
            with sessions() as session:
                session.execute(update(Job).where(Job.id == rerun.id).values(status="canceled"))

        pages.on_request(cancel)

        process_document_task(str(rerun.id))

        assert _key(document_id, 1) in storage.objects

    def test_storage_failure_on_the_file_is_unaffected(self, sessions, storage, pages, monkeypatch):
        pages.configure(1)
        _, job_id = _seed_job(sessions, 1)

        def unavailable(key):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(storage, "download_bytes", unavailable)

        with pytest.raises(requests.ConnectionError):
            process_document_task(str(job_id))

        assert storage.objects == {}
