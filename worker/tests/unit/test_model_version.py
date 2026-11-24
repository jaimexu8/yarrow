"""Unit tests for recording the model that produced a document's results (US-85)

The per-run model selection is a pure helper (_run_model) and the parser's
ModelInfo capture; the pipeline-specific tests moved to the new tasks.
"""

from unittest.mock import MagicMock

from app.core.config import settings
from app.pipeline.document_parser import DocumentParser, ModelInfo
from app.tasks.ingestion import _run_model

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
