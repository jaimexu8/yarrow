"""Focused unit tests for the async hybrid gateway.

Every test runs without the backend conftest so the gateway can be imported
straight from its source file with a minimal environment.
"""

import asyncio
import base64
import importlib.util
import io
import json
import logging
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

GATEWAY = (
    Path(__file__).resolve().parents[2] / "ocr_cluster" / "shared" / "hybrid_gateway.py"
)


def _load_gateway():
    spec = importlib.util.spec_from_file_location("hybrid_gateway_under_test", GATEWAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "PP-StructureV3+PaddleOCR-VL")
    monkeypatch.setenv("MODEL_VERSION", "3.2.0+vl-0.9b")
    monkeypatch.setenv("VLM_MAX_INFLIGHT", "16")
    return _load_gateway()


def _png(width=120, height=120):
    image = Image.effect_noise((width, height), 60).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _block(label, content="OLD", order=0, bbox=(0, 0, 100, 100)):
    return {
        "block_label": label,
        "block_content": content,
        "block_bbox": list(bbox),
        "block_order": order,
        "block_id": order + 1,
    }


def _structure_body(blocks):
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "parsing_res_list": list(blocks),
                        "overall_ocr_res": {},
                        "table_res_list": [],
                    }
                }
            ]
        }
    }


def _chat(text="ok", finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


def _models():
    return httpx.Response(200, json={"data": [{"id": "test-model"}]})


def _mock_client(gw, handler, model="test-model"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw.app.state.httpx_client = client
    gw.app.state.vlm_client = gw._DirectVLMClient(client, model=model)
    return client


def test_layout_parsing_reply_carries_model_headers(gateway):
    def handler(request):
        if request.url.path.endswith("/layout-parsing"):
            return httpx.Response(200, json=_structure_body([]))
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, handler)
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 200
    assert response.headers["X-Yarrow-Model-Name"] == "PP-StructureV3+PaddleOCR-VL"
    assert response.headers["X-Yarrow-Model-Version"] == "3.2.0+vl-0.9b"
    assert "result" in response.json()


def test_rejected_layout_parsing_reply_carries_model_headers(gateway):
    with TestClient(gateway.app) as client:
        response = client.post("/layout-parsing", json={"file": "", "fileType": 0})

    assert response.status_code == 400
    assert response.headers["X-Yarrow-Model-Version"] == "3.2.0+vl-0.9b"


def test_health_reply_has_no_model_headers(gateway):
    with TestClient(gateway.app) as client:
        response = client.get("/health")

    assert "X-Yarrow-Model-Name" not in response.headers


@pytest.mark.parametrize("missing", ["MODEL_NAME", "MODEL_VERSION"])
def test_gateway_refuses_to_start_without_model_identity(monkeypatch, missing):
    monkeypatch.setenv("MODEL_NAME", "PP-StructureV3+PaddleOCR-VL")
    monkeypatch.setenv("MODEL_VERSION", "3.2.0+vl-0.9b")
    monkeypatch.setenv(missing, " ")

    with pytest.raises(RuntimeError):
        _load_gateway()


def test_layout_parsing_replaces_structure_text_with_vlm_output(gateway):
    def handler(request):
        path = request.url.path
        if path.endswith("/models"):
            return _models()
        if path.endswith("/layout-parsing"):
            return httpx.Response(
                200, json=_structure_body([_block("text", "STRUCTURE TEXT")])
            )
        if path.endswith("/chat/completions"):
            return _chat("VLM TEXT")
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, handler)
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 200
    block = response.json()["result"]["layoutParsingResults"][0]["prunedResult"][
        "parsing_res_list"
    ][0]
    assert block["block_content"] == "VLM TEXT"
    assert block["vlm_failed"] is False


@pytest.mark.asyncio
async def test_vlm_semaphore_bounds_peak_inflight(gateway):
    gateway.app.state.vlm_sem = asyncio.Semaphore(2)
    active = 0
    peak = 0
    calls = 0
    lock = asyncio.Lock()

    async def handler(request):
        nonlocal active, peak, calls
        async with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return _chat()

    _mock_client(gateway, handler)

    results = await asyncio.gather(
        *[
            gateway._vlm_recognize_one(
                gateway.app,
                gateway._RequestContext("r"),
                _block("text"),
                "ocr",
                "AAAA",
            )
            for _ in range(8)
        ]
    )

    assert 1 <= peak <= 2
    assert calls == 8
    assert all(result[2]["text"] == "ok" for result in results)


@pytest.mark.asyncio
async def test_connection_error_retried_once(gateway):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom")
        return _chat("ok")

    _mock_client(gateway, handler)

    result = await gateway.app.state.vlm_client.recognize("AAAA", "ocr")

    assert calls == 2
    assert result["text"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [503, 429])
async def test_busy_status_retried_once(gateway, status):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status)
        return _chat("ok")

    _mock_client(gateway, handler)

    result = await gateway.app.state.vlm_client.recognize("AAAA", "ocr")

    assert calls == 2
    assert result["text"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_other_4xx_not_retried(gateway, status):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    _mock_client(gateway, handler)

    result = await gateway.app.state.vlm_client.recognize("AAAA", "ocr")

    assert calls == 1
    assert result is None


def test_fallback_keeps_structure_text_and_sets_vlm_failed(gateway):
    block = _block("text", "STRUCTURE TEXT")
    ctx = gateway._RequestContext("r")

    gateway._apply_vlm_result(block, None, ctx)

    assert block["block_content"] == "STRUCTURE TEXT"
    assert block["vlm_failed"] is True
    assert block["vlm_truncated"] is False
    assert ctx.failed_blocks == 1


@pytest.mark.asyncio
async def test_finish_reason_length_sets_vlm_truncated(gateway):
    _mock_client(gateway, lambda request: _chat("long", "length"))

    result = await gateway.app.state.vlm_client.recognize("AAAA", "table")
    block = _block("table", "STRUCTURE TABLE")
    ctx = gateway._RequestContext("r")
    gateway._apply_vlm_result(block, result, ctx)

    assert result["finish_reason"] == "length"
    assert block["vlm_truncated"] is True
    assert ctx.truncated_blocks == 1
    assert ctx.finish_reasons == {"table": {"length": 1}}


@pytest.mark.asyncio
async def test_repetition_guard_triggers_fallback_and_logs(gateway, caplog):
    loop_text = "the quick brown fox jumps" * 12
    _mock_client(gateway, lambda request: _chat(loop_text))

    with caplog.at_level(logging.WARNING, logger="hybrid_gateway"):
        result = await gateway.app.state.vlm_client.recognize("AAAA", "formula")
    block = _block("formula", "STRUCTURE FORMULA")
    ctx = gateway._RequestContext("r")
    gateway._apply_vlm_result(block, result, ctx)

    assert result["loop"] is True
    assert block["block_content"] == "STRUCTURE FORMULA"
    assert block["vlm_failed"] is True
    assert ctx.loop_blocks == 1
    assert "vlm_repetition_loop" in caplog.text


@pytest.mark.asyncio
async def test_structure_call_queue_timeout(gateway):
    gateway.STRUCTURE_QUEUE_TIMEOUT = 0.01
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    gateway.app.state.structure_sem = sem

    body, error = await gateway._structure_call(
        gateway.app, {}, {}, gateway._RequestContext("r")
    )

    assert body is None
    assert error == "timeout"


def test_structure_queue_timeout_returns_503_with_retry_after(gateway, monkeypatch):
    async def fake_structure_call(*args, **kwargs):
        return None, "timeout"

    monkeypatch.setattr(gateway, "_structure_call", fake_structure_call)

    with TestClient(gateway.app) as client:
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 503
    assert "Retry-After" in response.headers


def test_deadline_cancels_vlm_and_frees_slot(gateway):
    gateway.GATEWAY_DEADLINE = 0.2
    started = threading.Event()

    async def handler(request):
        path = request.url.path
        if path.endswith("/models"):
            return _models()
        if path.endswith("/layout-parsing"):
            return httpx.Response(200, json=_structure_body([_block("text")]))
        if path.endswith("/chat/completions"):
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, handler)
        gateway.app.state.vlm_sem = asyncio.Semaphore(gateway.VLM_MAX_INFLIGHT)
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

        async def slot_is_free():
            sem = gateway.app.state.vlm_sem
            try:
                await asyncio.wait_for(sem.acquire(), 0.3)
            except TimeoutError:
                return False
            sem.release()
            return True

        freed = client.portal.call(slot_is_free)

    assert response.status_code == 504
    assert started.is_set()
    assert freed is True


def test_tables_submitted_before_formulas_before_text(gateway):
    order = []

    async def handler(request):
        path = request.url.path
        if path.endswith("/models"):
            return _models()
        if path.endswith("/layout-parsing"):
            blocks = [
                _block("text", "TEXT", 0),
                _block("formula", "FORMULA", 1),
                _block("table", "TABLE", 2),
            ]
            return httpx.Response(200, json=_structure_body(blocks))
        if path.endswith("/chat/completions"):
            payload = json.loads(request.content)
            order.append(payload["messages"][0]["content"][1]["text"])
            return _chat("NEW")
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        gateway.app.state.vlm_sem = asyncio.Semaphore(1)
        _mock_client(gateway, handler)
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 200
    assert order == ["Table Recognition:", "Formula Recognition:", "OCR:"]


def test_event_loop_stays_responsive_during_large_crop(gateway):
    crop_started = threading.Event()
    release = threading.Event()
    original_crop = gateway._crop_to_b64_jpeg

    def slow_crop(*args, **kwargs):
        crop_started.set()
        release.wait(timeout=10)
        return original_crop(*args, **kwargs)

    gateway._crop_to_b64_jpeg = slow_crop

    async def handler(request):
        path = request.url.path
        if path.endswith("/models"):
            return _models()
        if path.endswith("/layout-parsing"):
            return httpx.Response(200, json=_structure_body([_block("text")]))
        if path.endswith("/chat/completions"):
            return _chat("NEW")
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, handler)
        result = {}

        def post_page():
            result["response"] = client.post(
                "/layout-parsing", json={"file": _png(500, 500), "fileType": 1}
            )

        worker = threading.Thread(target=post_page)
        worker.start()
        assert crop_started.wait(timeout=10)
        started = time.perf_counter()
        health = client.get("/health")
        health_ms = (time.perf_counter() - started) * 1000
        release.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert result["response"].status_code == 200
    assert health.status_code == 200
    assert health_ms < 200


def test_ready_succeeds_and_fails_with_upstreams(gateway):
    probed_paths = []

    def ok_handler(request):
        probed_paths.append(request.url.path)
        if request.url.path.endswith("/models"):
            return _models()
        return httpx.Response(200, json={"status": "ok"})

    with TestClient(gateway.app) as client:
        _mock_client(gateway, ok_handler)
        assert client.get("/ready").status_code == 200
    assert "/ready" in probed_paths
    assert "/layout-parsing" not in probed_paths

    def vlm_down(request):
        if request.url.path.endswith("/models"):
            raise httpx.ConnectError("down")
        return httpx.Response(200)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, vlm_down)
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["upstream"] == "vlm"

    def structure_down(request):
        raise httpx.ConnectError("down")

    with TestClient(gateway.app) as client:
        _mock_client(gateway, structure_down)
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["upstream"] == "structure"


def test_simple_otsl_table_becomes_html(gateway):
    html = gateway.convert_otsl_to_html("<fcel>A<fcel>B<nl><fcel>C<fcel>D<nl>")

    assert html == (
        "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    )


def test_already_html_table_is_left_unchanged(gateway):
    existing = "<table><tr><td>x</td></tr></table>"

    assert gateway._normalize_direct_table_content(existing) == existing


def test_otsl_empty_input_is_safe(gateway):
    assert gateway.convert_otsl_to_html("") == ""
    assert gateway._normalize_direct_table_content("") == ""
    assert gateway._normalize_direct_table_content("   ") == "   "


def test_otsl_row_and_col_span_attributes(gateway):
    html = gateway.convert_otsl_to_html("<fcel>H<xcel>x<nl><ucel>y<fcel>z<nl>")

    assert html == (
        '<table><tr><td rowspan="2" colspan="2">H</td></tr><tr><td>z</td></tr></table>'
    )


def test_direct_table_block_is_converted_to_html(gateway):
    otsl = "<fcel>Item<fcel>Qty<nl><fcel>Bolt<fcel>4<nl>"

    def handler(request):
        path = request.url.path
        if path.endswith("/models"):
            return _models()
        if path.endswith("/layout-parsing"):
            return httpx.Response(200, json=_structure_body([_block("table", "OLD")]))
        if path.endswith("/chat/completions"):
            return _chat(otsl)
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        _mock_client(gateway, handler)
        response = client.post("/layout-parsing", json={"file": _png(), "fileType": 1})

    assert response.status_code == 200
    page = response.json()["result"]["layoutParsingResults"][0]
    block = page["prunedResult"]["parsing_res_list"][0]
    assert block["block_content"] == (
        "<table><tr><td>Item</td><td>Qty</td></tr>"
        "<tr><td>Bolt</td><td>4</td></tr></table>"
    )
    # Markdown assembly sees HTML, not raw OTSL, and emits a pipe table.
    assert "<fcel>" not in page["markdown"]["text"]
    assert "| Item | Qty |" in page["markdown"]["text"]
