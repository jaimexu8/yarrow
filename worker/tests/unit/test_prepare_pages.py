"""Unit tests for prepare_pages and the parsing gate.

prepare_pages loads one batch of pages, seeds its rows and uploads its images.
_maybe_start_parsing may only flip a preparing job to parsing when the set of
queued pages equals the set of run pages.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from yarrow_db.models import Job, JobPage, Page, Warning

from app.pipeline.document_parser import DocumentParser
from app.pipeline.text_layer import TextLayerDecision, Word
from app.tasks import ingestion
from app.tasks.ingestion import _utcnow, parse_page

PARSE = "app.tasks.ingestion.parse_page"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"

# The gateway fixture replaces apply_manual_rotations with a recorder that never
# decodes pages. Keep the production implementation so the out-of-batch rotation
# test exercises the real base64/PIL path that crashes.
_REAL_APPLY_MANUAL_ROTATIONS = DocumentParser.apply_manual_rotations


def _pin_batch_size(monkeypatch, size):
    try:
        monkeypatch.setattr(
            ingestion.settings, "INGEST_PAGE_BATCH_SIZE", size, raising=False
        )
    except ValueError:
        monkeypatch.setattr(
            ingestion.settings,
            "__dict__",
            {**vars(ingestion.settings), "INGEST_PAGE_BATCH_SIZE": size},
        )


def _preparing_job(sessions, seed, page_count):
    """A processing job in the preparing stage, with no job pages yet."""
    document, job = seed(page_count=page_count, status="processing")
    with sessions() as session:
        job = session.get(Job, job.id)
        job.status = "processing"
        job.document.status = "processing"
        job.current_stage = "preparing"
        job.total_pages = page_count
    return document, job


def _parse_enqueues(enqueue):
    return [args[1] for name, _queue, args in enqueue if name == PARSE]


class TestPreparePages:
    def test_seeds_one_batch_without_flipping_the_gate(
        self, sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=4)
        gateway.configure(pages=4, rotations={1: 90.0})
        storage.store[document.storage_key] = b"file"

        ingestion.prepare_pages(str(job.id), [1, 2])

        with sessions() as session:
            pages = {
                row.page_number: row
                for row in session.query(Page).filter(Page.document_id == document.id)
            }
            assert set(pages) == {1, 2}
            assert all(pages[number].status == "pending" for number in (1, 2))
            assert pages[1].page_image_key == f"documents/{document.id}/pages/1.png"
            assert pages[2].page_image_key is None
            for number in (1, 2):
                assert pages[number].thumbnail_key == (
                    f"documents/{document.id}/thumbnails/{number}.webp"
                )
            job_pages = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert sorted(row.page_number for row in job_pages) == [1, 2]
            assert all(row.status == "queued" for row in job_pages)
            assert all(row.queued_at is not None for row in job_pages)
            warning_pages = {
                session.get(Page, warning.page_id).page_number
                for warning in session.query(Warning).all()
            }
            assert warning_pages == {1}

        for number in (1, 2):
            assert f"documents/{document.id}/rasters/{number}.png" in storage.store
            assert f"documents/{document.id}/thumbnails/{number}.webp" in storage.store
        assert f"documents/{document.id}/pages/1.png" in storage.store
        assert f"documents/{document.id}/pages/2.png" not in storage.store

        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "preparing"
        assert _parse_enqueues(enqueue) == []

    def test_manual_rotation_outside_batch_does_not_crash(
        self, sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
    ):
        # Use the real apply_manual_rotations: the gateway stub records rotations
        # without decoding them, so it would hide the PIL/base64 crash.
        monkeypatch.setattr(
            DocumentParser, "apply_manual_rotations", _REAL_APPLY_MANUAL_ROTATIONS
        )
        document, job = _preparing_job(sessions, seed, page_count=4)
        gateway.configure(pages=4)
        storage.store[document.storage_key] = b"file"
        with sessions() as session:
            session.add(
                Page(
                    document_id=document.id,
                    page_number=4,
                    manual_rotation_deg=90,
                    status="pending",
                )
            )

        ingestion.prepare_pages(str(job.id), [1, 2])

        with sessions() as session:
            job_pages = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert sorted(row.page_number for row in job_pages) == [1, 2]
            page4 = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 4)
                .one()
            )
            assert page4.manual_rotation_deg == 90
            assert page4.status == "pending"

    def test_all_batches_flip_gate_and_enqueue_every_queued_page(
        self, sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        document, job = _preparing_job(sessions, seed, page_count=4)
        gateway.configure(pages=4)
        storage.store[document.storage_key] = b"file"
        redis_stub.store[f"page_deliveries:{job.id}:1"] = 3

        ingestion.prepare_pages(str(job.id), [1, 2])
        assert _parse_enqueues(enqueue) == []
        ingestion.prepare_pages(str(job.id), [3, 4])

        with sessions() as session:
            job = session.get(Job, job.id)
            assert job.current_stage == "parsing"
            rows = session.query(JobPage).filter(JobPage.job_id == job.id).all()
            assert sorted(row.page_number for row in rows) == [1, 2, 3, 4]
            assert all(row.status == "queued" for row in rows)
        assert sorted(_parse_enqueues(enqueue)) == [1, 2, 3, 4]
        assert all(queue == "ocr" for name, queue, _args in enqueue if name == PARSE)
        assert f"page_deliveries:{job.id}:1" not in redis_stub.store

    def test_duplicate_batch_cannot_flip_the_gate_early(
        self, sessions, storage, gateway, enqueue, redis_stub, seed, monkeypatch
    ):
        _pin_batch_size(monkeypatch, 2)
        document, job = _preparing_job(sessions, seed, page_count=4)
        gateway.configure(pages=4)
        storage.store[document.storage_key] = b"file"

        ingestion.prepare_pages(str(job.id), [1, 2])
        ingestion.prepare_pages(str(job.id), [1, 2])

        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "preparing"
            assert (
                session.query(JobPage)
                .filter(JobPage.job_id == job.id, JobPage.page_number == 1)
                .count()
                == 1
            )
        assert _parse_enqueues(enqueue) == []

        ingestion.prepare_pages(str(job.id), [3, 4])
        assert sorted(_parse_enqueues(enqueue)) == [1, 2, 3, 4]

    def test_gate_uses_set_equality_not_a_raw_count(
        self, sessions, storage, gateway, enqueue, redis_stub, seed
    ):
        document, job = _preparing_job(sessions, seed, page_count=3)
        gateway.configure(pages=3)
        storage.store[document.storage_key] = b"file"
        # All three run pages have rows, but page 3 is not queued yet. A count
        # of queued rows would still be wrong (2 != 3); so would a count that
        # matches a wrong set of page numbers.
        with sessions() as session:
            job = session.get(Job, job.id)
            session.add_all(
                [
                    JobPage(
                        job_id=job.id,
                        page_number=number,
                        status="queued" if number in (1, 2) else "processing",
                        queued_at=_utcnow(),
                    )
                    for number in (1, 2, 3)
                ]
            )

        ingestion._maybe_start_parsing(str(job.id))

        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "preparing"
        assert _parse_enqueues(enqueue) == []

        with sessions() as session:
            session.query(JobPage).filter(
                JobPage.job_id == job.id, JobPage.page_number == 3
            ).update({"status": "queued"})

        ingestion._maybe_start_parsing(str(job.id))

        with sessions() as session:
            assert session.get(Job, job.id).current_stage == "parsing"
        assert _parse_enqueues(enqueue) == [1, 2, 3]

    def test_prepare_pages_is_an_ocr_task(self):
        assert ingestion.prepare_pages.name == "app.tasks.ingestion.prepare_pages"
        assert ingestion.prepare_pages.queue == "ocr"

    def test_missing_raster_is_still_a_permanent_failure(
        self, sessions, storage, gateway, seed
    ):
        document, job = seed(page_count=1, status="processing")
        gateway.configure(pages=1)
        with sessions() as session:
            job = session.get(Job, job.id)
            job.current_stage = "parsing"
            job.total_pages = 1
            session.add(
                JobPage(job_id=job.id, page_number=1, status="queued", queued_at=None)
            )
            session.add(Page(document_id=document.id, page_number=1, status="pending"))
        # No raster uploaded -> download raises ObjectNotFoundError.

        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "failed"


@pytest.fixture
def text_layer(monkeypatch):
    """Stub the text-layer API so the gate's thresholds are not under test here."""
    fake = FakeTextLayer()
    monkeypatch.setattr(ingestion.settings, "TEXT_LAYER_ENABLED", True)
    monkeypatch.setattr(ingestion, "extract_page_text", fake.extract_page_text)
    monkeypatch.setattr(ingestion, "evaluate_text_layer", fake.evaluate_text_layer)
    monkeypatch.setattr(ingestion, "scale_words", fake.scale_words)
    monkeypatch.setattr(ingestion, "serialize_words", fake.serialize_words)
    return fake


class FakeTextLayer:
    def __init__(self):
        self.usable: set[int] = set()
        self.fail_extract: set[int] = set()
        self.extracted: list[int] = []
        self.word = Word(1.0, 2.0, 3.0, 4.0, "embedded", 0, 0, 0)

    def extract_page_text(self, file_data, page_number):
        self.extracted.append(page_number)
        if page_number in self.fail_extract:
            raise RuntimeError("extractor exploded")
        return SimpleNamespace(page_number=page_number, words=[self.word])

    def evaluate_text_layer(self, page_text):
        usable = page_text.page_number in self.usable
        return TextLayerDecision(usable, "ok" if usable else "rejected: stub")

    def scale_words(self, page_text):
        return list(page_text.words)

    def serialize_words(self, words):
        return {
            "words": [
                [w.x0, w.y0, w.x1, w.y1, w.text, w.block_no, w.line_no, w.word_no]
                for w in words
            ]
        }


class TestTextLayerExtraction:
    def test_usable_pdf_page_uploads_serialized_words(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=2)
        gateway.configure(pages=2)
        storage.store[document.storage_key] = PDF_BYTES
        text_layer.usable = {1, 2}

        ingestion.prepare_pages(str(job.id), [1, 2])

        assert text_layer.extracted == [1, 2]
        for number in (1, 2):
            key = f"documents/{document.id}/text_layer/{number}.json"
            assert json.loads(storage.store[key]) == {
                "words": [[1.0, 2.0, 3.0, 4.0, "embedded", 0, 0, 0]]
            }

    def test_rejected_page_writes_no_text_layer(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=2)
        gateway.configure(pages=2)
        storage.store[document.storage_key] = PDF_BYTES
        text_layer.usable = {1}

        ingestion.prepare_pages(str(job.id), [1, 2])

        assert f"documents/{document.id}/text_layer/1.json" in storage.store
        assert f"documents/{document.id}/text_layer/2.json" not in storage.store

    def test_forced_ml_job_writes_no_text_layer(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=1)
        gateway.configure(pages=1)
        storage.store[document.storage_key] = PDF_BYTES
        text_layer.usable = {1}
        with sessions() as session:
            session.get(Job, job.id).skip_text_layer = True

        ingestion.prepare_pages(str(job.id), [1])

        assert text_layer.extracted == []
        assert f"documents/{document.id}/text_layer/1.json" not in storage.store

    def test_non_pdf_input_writes_no_text_layer(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=1)
        gateway.configure(pages=1)
        storage.store[document.storage_key] = b"not a pdf"
        text_layer.usable = {1}

        ingestion.prepare_pages(str(job.id), [1])

        assert text_layer.extracted == []
        assert f"documents/{document.id}/text_layer/1.json" not in storage.store

    def test_storage_failure_does_not_fail_the_batch(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch, caplog
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=1)
        gateway.configure(pages=1)
        storage.store[document.storage_key] = PDF_BYTES
        text_layer.usable = {1}
        original_upload = storage.upload_file

        def failing_upload(file, key):
            if "/text_layer/" in key:
                raise OSError("bucket unavailable")
            return original_upload(file, key)

        monkeypatch.setattr(storage, "upload_file", failing_upload)

        with caplog.at_level(logging.ERROR):
            ingestion.prepare_pages(str(job.id), [1])

        assert f"documents/{document.id}/rasters/1.png" in storage.store
        assert f"documents/{document.id}/text_layer/1.json" not in storage.store
        assert "Could not store text layer" in caplog.text
        with sessions() as session:
            assert (
                session.query(JobPage)
                .filter(JobPage.job_id == job.id, JobPage.page_number == 1)
                .one()
                .status
                == "queued"
            )

    def test_extraction_error_does_not_fail_the_batch(
        self, sessions, storage, gateway, seed, text_layer, monkeypatch, caplog
    ):
        monkeypatch.setattr(ingestion, "build_thumbnail", lambda _data: b"thumb")
        document, job = _preparing_job(sessions, seed, page_count=1)
        gateway.configure(pages=1)
        storage.store[document.storage_key] = PDF_BYTES
        text_layer.fail_extract = {1}

        with caplog.at_level(logging.ERROR):
            ingestion.prepare_pages(str(job.id), [1])

        assert f"documents/{document.id}/rasters/1.png" in storage.store
        assert f"documents/{document.id}/text_layer/1.json" not in storage.store
        assert "Could not store text layer" in caplog.text
