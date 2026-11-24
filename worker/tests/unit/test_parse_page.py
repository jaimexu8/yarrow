"""Unit tests for parse_page."""

import json

import pytest
import requests
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from yarrow_db.models import Job, JobPage, Page, Warning

from app.pipeline.text_layer import Word, serialize_words
from app.tasks import ingestion
from app.tasks.ingestion import parse_page


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=response)


def _seed_job(sessions, seed, page_count):
    """A processing job with queued job_pages for every page and pending
    page rows, so parse_page finds the row to FOR UPDATE."""
    document, job = seed(page_count=page_count, status="processing")
    document.status = "processing"
    with sessions() as session:
        job = session.get(Job, job.id)
        job.status = "processing"
        job.current_stage = "parsing"
        job.total_pages = page_count
        for n in range(1, page_count + 1):
            session.add(
                JobPage(job_id=job.id, page_number=n, status="queued", queued_at=None)
            )
            session.add(Page(document_id=document.id, page_number=n, status="pending"))
    return document, job


class TestParsePage:
    def test_success_commits_page_and_progress(
        self, sessions, storage, gateway, enqueue, seed
    ):
        document, job = _seed_job(sessions, seed, 2)
        gateway.configure(pages=2)
        for n in (1, 2):
            storage.store[f"documents/{document.id}/rasters/{n}.png"] = b"raster"

        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "completed"
            assert page.model_name == "M1"
            job = session.get(Job, job.id)
            # 1 of 2 terminal, so no finalize yet.
            assert job.pages_processed == 1
        assert not any(
            name == "app.tasks.ingestion.finalize_document" for (name, _, _) in enqueue
        )

    def test_last_page_triggers_finalize(
        self, sessions, storage, gateway, enqueue, seed
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        parse_page(str(job.id), 1)

        with sessions() as session:
            assert session.get(Job, job.id).pages_processed == 1
        assert [
            (name, queue)
            for (name, queue, _) in enqueue
            if name == "app.tasks.ingestion.finalize_document"
        ] == [("app.tasks.ingestion.finalize_document", "default")]

    def test_permanent_422_fails_without_retry(
        self, sessions, storage, gateway, enqueue, seed
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        gateway.fail(1, _http_error(422))
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        parse_page(str(job.id), 1)  # no retry raised

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "failed"
            assert page.error_code == "inference_error"

    def test_retriable_500_retries_then_exhausts_fails(
        self, sessions, storage, gateway, enqueue, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        gateway.fail(1, _http_error(500))
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        # First delivery: retries left (retries=0 < 3) -> self.retry() raises
        # the raw error.
        with pytest.raises(requests.HTTPError):
            parse_page(str(job.id), 1)

        # Exhaust the retries: the page is then marked failed, no raise.
        monkeypatch.setattr(parse_page, "max_retries", 0)
        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "failed"
            assert page.error_code == "inference_error"

    def test_timeout_is_retriable(self, sessions, storage, gateway, seed):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        gateway.fail(1, requests.Timeout("slow"))
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        with pytest.raises(requests.Timeout):
            parse_page(str(job.id), 1)

    def test_missing_raster_is_permanent(self, sessions, storage, gateway, seed):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        # No raster uploaded -> download raises ObjectNotFoundError.

        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "failed"

    def test_delivery_cap_kicks_in(
        self, sessions, storage, gateway, enqueue, redis_stub, seed
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        # Pre-set the counter just above the cap (6).
        redis_stub.store[f"page_deliveries:{job.id}:1"] = 6

        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "failed"
            assert page.error_code == "unknown"
        # The gateway was never called.
        assert gateway.calls == []

    def test_terminal_page_is_a_noop(
        self, sessions, storage, gateway, enqueue, redis_stub, seed
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        with sessions() as session:
            session.query(Page).filter(
                Page.document_id == document.id, Page.page_number == 1
            ).update({"status": "completed"})

        parse_page(str(job.id), 1)

        # The gateway was never called (early exit).
        assert gateway.calls == []

    def test_canceled_job_is_a_noop(self, sessions, storage, gateway, seed):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        with sessions() as session:
            session.execute(
                update(Job).where(Job.id == job.id).values(status="canceled")
            )

        parse_page(str(job.id), 1)

        assert gateway.calls == []

    def test_integrity_23503_is_swallowed(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        def raise_fk(job_id, page_no, document_id, parser, objects):
            orig = Exception("FK violation")
            orig.pgcode = "23503"
            raise IntegrityError("stmt", {}, orig)

        monkeypatch.setattr("app.tasks.ingestion._commit_success", raise_fk)

        # Should not raise; the page stays pending (the transaction rolled back).
        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "pending"

    def test_integrity_23505_is_swallowed(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        def raise_unique(job_id, page_no, document_id, parser, objects):
            orig = Exception("unique violation")
            orig.pgcode = "23505"
            raise IntegrityError("stmt", {}, orig)

        monkeypatch.setattr("app.tasks.ingestion._commit_success", raise_unique)

        parse_page(str(job.id), 1)

        with sessions() as session:
            page = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .one()
            )
            assert page.status == "pending"

    def test_preserves_ingest_warning_and_inserts_its_own(
        self, sessions, storage, gateway, enqueue, seed
    ):
        """parse_page inserts its own quality warnings but never deletes the
        auto-rotation warning ingest already recorded (delete_warnings=False)."""
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1, warnings={0: "Image is blurry"})
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        # Ingest recorded an auto-rotation warning on the page before parse_page.
        with sessions() as session:
            (page,) = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .all()
            )
            session.add(
                Warning(
                    page_id=page.id,
                    warning_type="quality",
                    message="Page was auto-rotated by 90°",
                )
            )

        parse_page(str(job.id), 1)

        with sessions() as session:
            (page,) = (
                session.query(Page)
                .filter(Page.document_id == document.id, Page.page_number == 1)
                .all()
            )
            messages = sorted(
                session.scalars(
                    select(Warning.message).where(Warning.page_id == page.id)
                )
            )
        assert messages == ["Image is blurry", "Page was auto-rotated by 90°"]


class TestParsePageTextLayer:
    def test_stored_text_layer_is_loaded_into_the_parser(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        word = Word(1.0, 2.0, 3.0, 4.0, "embedded", 0, 0, 0)
        storage.store[f"documents/{document.id}/text_layer/1.json"] = json.dumps(
            serialize_words([word])
        ).encode()
        monkeypatch.setattr(ingestion.settings, "TEXT_LAYER_ENABLED", True)

        parse_page(str(job.id), 1)

        assert gateway.calls == [1]
        assert [w.text for w in gateway.text_layer_words[0]] == ["embedded"]

    def test_missing_text_layer_is_ignored(self, sessions, storage, gateway, seed):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"

        parse_page(str(job.id), 1)

        assert gateway.calls == [1]
        assert gateway.text_layer_words == [[]]

    def test_skip_text_layer_prevents_loading_even_when_stored(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        word = Word(1.0, 2.0, 3.0, 4.0, "embedded", 0, 0, 0)
        storage.store[f"documents/{document.id}/text_layer/1.json"] = json.dumps(
            serialize_words([word])
        ).encode()
        monkeypatch.setattr(ingestion.settings, "TEXT_LAYER_ENABLED", True)
        with sessions() as session:
            session.get(Job, job.id).skip_text_layer = True

        parse_page(str(job.id), 1)

        assert gateway.calls == [1]
        assert gateway.text_layer_words == [[]]

    def test_malformed_text_layer_is_ignored(
        self, sessions, storage, gateway, seed, monkeypatch
    ):
        document, job = _seed_job(sessions, seed, 1)
        gateway.configure(pages=1)
        storage.store[f"documents/{document.id}/rasters/1.png"] = b"raster"
        storage.store[f"documents/{document.id}/text_layer/1.json"] = b"not json"
        monkeypatch.setattr(ingestion.settings, "TEXT_LAYER_ENABLED", True)

        parse_page(str(job.id), 1)

        assert gateway.calls == [1]
        assert gateway.text_layer_words == [[]]
