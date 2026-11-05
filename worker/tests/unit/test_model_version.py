"""Unit tests for recording the model that produced a document's results (US-85)"""

from unittest.mock import MagicMock

import pytest
from yarrow_db.models import Page

from app.core.config import PIPELINE_VERSION, settings
from app.pipeline.document_parser import DocumentParser, ModelInfo
from app.tasks.ingestion import AllPagesFailedError, _run_model, process_document_task

CURRENT = ModelInfo("PP-StructureV3+PaddleOCR-VL", "3.2.0+vl-0.9b")
PREVIOUS = ModelInfo("PP-StructureV3+PaddleOCR-VL", "3.1.4+vl-0.9b")


def _respond(monkeypatch, headers):
    response = MagicMock()
    response.headers = headers
    response.json.return_value = {"result": {}}
    monkeypatch.setattr("app.pipeline.document_parser.requests.post", lambda *args, **kwargs: response)


def test_parser_records_model_from_response_headers(monkeypatch):
    _respond(monkeypatch, {"X-Yarrow-Model-Name": CURRENT.name, "X-Yarrow-Model-Version": CURRENT.version})
    parser = DocumentParser()

    parser.process_page("page", 2)

    assert parser.page_models == {2: CURRENT}


def test_parser_falls_back_to_settings_when_headers_missing(monkeypatch):
    _respond(monkeypatch, {})
    parser = DocumentParser()

    parser.process_page("page", 0)

    assert parser.page_models == {0: ModelInfo(settings.INFERENCE_MODEL_NAME, settings.INFERENCE_MODEL_VERSION)}


def test_parser_records_nothing_for_failed_page(monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr("app.pipeline.document_parser.requests.post", fail)
    parser = DocumentParser()

    parser._process_page_isolated(0, "page")

    assert parser.page_models == {}
    assert parser.failed_page_numbers == [1]


def test_run_model_takes_version_reported_by_most_pages():
    assert _run_model({0: PREVIOUS, 1: CURRENT, 2: CURRENT}) == CURRENT
    assert _run_model({0: CURRENT, 1: PREVIOUS, 2: PREVIOUS}) == PREVIOUS


def test_run_model_breaks_ties_by_later_response():
    assert _run_model({1: PREVIOUS, 0: CURRENT}) == CURRENT
    assert _run_model({0: CURRENT, 1: PREVIOUS}) == PREVIOUS


def test_run_model_is_none_without_completed_pages():
    assert _run_model({}) is None


def test_run_records_model_on_pages_job_and_document(job, session, storage, parser):
    parser.configure(page_count=3, models={0: PREVIOUS, 1: CURRENT, 2: CURRENT})

    process_document_task(str(job.id))

    pages = sorted((obj for obj in session.added if isinstance(obj, Page)), key=lambda page: page.page_number)
    assert [(page.model_name, page.model_version) for page in pages] == [PREVIOUS, CURRENT, CURRENT]
    assert (job.model_name, job.model_version, job.pipeline_version) == (*CURRENT, PIPELINE_VERSION)
    document = job.document
    assert (document.model_name, document.model_version, document.pipeline_version) == (*CURRENT, PIPELINE_VERSION)
    assert document.processed_at is not None


def test_failed_page_records_no_model(job, session, storage, parser):
    parser.configure(page_count=2, failed=(2,), models={0: CURRENT, 1: CURRENT})

    process_document_task(str(job.id))

    pages = sorted((obj for obj in session.added if isinstance(obj, Page)), key=lambda page: page.page_number)
    assert [(page.model_name, page.model_version) for page in pages] == [CURRENT, (None, None)]
    assert job.document.status == "completed"
    assert (job.document.model_name, job.document.model_version) == CURRENT


def test_reprocess_with_new_version_updates_document(job, session, storage, parser):
    parser.configure(page_count=2, models={0: PREVIOUS, 1: PREVIOUS})
    process_document_task(str(job.id))
    first_processed_at = job.document.processed_at
    assert (job.document.model_name, job.document.model_version) == PREVIOUS

    parser.configure(page_count=2, models={0: CURRENT, 1: CURRENT})
    process_document_task(str(job.id))

    assert (job.model_name, job.model_version) == CURRENT
    assert (job.document.model_name, job.document.model_version) == CURRENT
    assert job.document.processed_at >= first_processed_at


def test_fully_failed_run_keeps_document_model(job, session, storage, parser):
    parser.configure(page_count=2, models={0: PREVIOUS, 1: PREVIOUS})
    process_document_task(str(job.id))
    processed_at = job.document.processed_at

    parser.configure(page_count=2, failed=(1, 2), models={0: CURRENT, 1: CURRENT})
    with pytest.raises(AllPagesFailedError):
        process_document_task(str(job.id))

    assert (job.model_name, job.model_version) == (None, None)
    assert (job.document.model_name, job.document.model_version) == PREVIOUS
    assert job.document.pipeline_version == PIPELINE_VERSION
    assert job.document.processed_at == processed_at
