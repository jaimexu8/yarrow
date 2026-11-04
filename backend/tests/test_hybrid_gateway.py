"""The inference gateway reports its model identity in response headers (US-85)"""

import base64
import importlib.util
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

GATEWAY = Path(__file__).resolve().parents[2] / "ocr_cluster" / "shared" / "hybrid_gateway.py"


def _load_gateway():
    spec = importlib.util.spec_from_file_location("hybrid_gateway_under_test", GATEWAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "PP-StructureV3+PaddleOCR-VL")
    monkeypatch.setenv("MODEL_VERSION", "3.2.0+vl-0.9b")
    return _load_gateway()


def _png() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class _StructureResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"layoutParsingResults": [{"prunedResult": {"parsing_res_list": []}}]}}


def test_layout_parsing_reply_carries_model_headers(gateway, monkeypatch):
    monkeypatch.setattr(gateway.requests, "post", lambda *args, **kwargs: _StructureResponse())

    response = TestClient(gateway.app).post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 200
    assert response.headers["X-Yarrow-Model-Name"] == "PP-StructureV3+PaddleOCR-VL"
    assert response.headers["X-Yarrow-Model-Version"] == "3.2.0+vl-0.9b"
    assert "result" in response.json()


def test_rejected_layout_parsing_reply_carries_model_headers(gateway):
    response = TestClient(gateway.app).post("/layout-parsing", json={"file": "", "fileType": 0})

    assert response.status_code == 400
    assert response.headers["X-Yarrow-Model-Version"] == "3.2.0+vl-0.9b"


def test_health_reply_has_no_model_headers(gateway):
    response = TestClient(gateway.app).get("/health")

    assert "X-Yarrow-Model-Name" not in response.headers


@pytest.mark.parametrize("missing", ["MODEL_NAME", "MODEL_VERSION"])
def test_gateway_refuses_to_start_without_model_identity(monkeypatch, missing):
    monkeypatch.setenv("MODEL_NAME", "PP-StructureV3+PaddleOCR-VL")
    monkeypatch.setenv("MODEL_VERSION", "3.2.0+vl-0.9b")
    monkeypatch.setenv(missing, " ")

    with pytest.raises(RuntimeError):
        _load_gateway()
