"""Property-based tests for the direct-only OCR gateway.

The invariants here are checked against the small pure helpers and the real
request path, so they keep holding while the gateway's internals are trimmed.
"""

import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageStat

GATEWAY = (
    Path(__file__).resolve().parents[2] / "ocr_cluster" / "shared" / "hybrid_gateway.py"
)


def _load_gateway():
    spec = importlib.util.spec_from_file_location(
        "hybrid_gateway_properties_under_test", GATEWAY
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "PP-StructureV3+PaddleOCR-VL")
    monkeypatch.setenv("MODEL_VERSION", "3.2.0+vl-0.9b")
    return _load_gateway()


PROPS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

SMALL_PROPS = settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _noise_png(width=120, height=120):
    image = Image.effect_noise((width, height), 60).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _block(label, order=0, bbox=(10, 10, 100, 100)):
    return {
        "block_label": label,
        "block_content": "STRUCT",
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


def _chat(text="ok"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        },
    )


# --- 1. OTSL conversion ----------------------------------------------------

_CELL_TAGS = ["<fcel>", "<ecel>", "<lcel>", "<ucel>", "<xcel>"]
# Cell text is always non-empty: the converter reads the token after <fcel>
# as that cell's text, so an empty chunk would make it misread the next tag.
_OTSL_TEXT = st.text(alphabet="abcd12", min_size=1, max_size=4)


@st.composite
def otsl_strings(draw):
    rows = draw(
        st.lists(
            st.lists(st.sampled_from(_CELL_TAGS), min_size=0, max_size=4),
            min_size=0,
            max_size=4,
        )
    )
    chunks = []
    for row in rows:
        for tag in row:
            chunks.append(tag)
            chunks.append(draw(_OTSL_TEXT))
        chunks.append("<nl>")
    return "".join(chunks)


@PROPS
@given(otsl=otsl_strings())
def test_otsl_conversion_is_safe_and_html_round_trips(gateway, otsl):
    html = gateway.convert_otsl_to_html(otsl)

    assert html == "" or html.startswith("<table>")
    if html:
        assert gateway._normalize_direct_table_content(html) == html


# --- 2. Bounding boxes ------------------------------------------------------


@st.composite
def images_and_bboxes(draw):
    width = draw(st.integers(min_value=40, max_value=120))
    height = draw(st.integers(min_value=40, max_value=120))
    # Start corners may be negative, end corners may run past the image, but
    # the box overlaps the image so clamping keeps a non-empty crop.
    x0 = draw(st.integers(min_value=-20, max_value=width - 1))
    y0 = draw(st.integers(min_value=-20, max_value=height - 1))
    x1 = draw(st.integers(min_value=max(1, x0 + 1), max_value=width + 40))
    y1 = draw(st.integers(min_value=max(1, y0 + 1), max_value=height + 40))
    return width, height, (x0, y0, x1, y1)


def _assume_overlapping(image, bbox):
    x0, y0, x1, y1 = bbox
    assume(x0 <= x1 and y0 <= y1)
    assume(x1 > 0 and y1 > 0)
    assume(x0 < image.width and y0 < image.height)


@PROPS
@given(case=images_and_bboxes())
def test_clamp_bbox_stays_inside_image_and_keeps_order(gateway, case):
    width, height, bbox = case
    image = Image.new("RGB", (width, height), (40, 80, 120))
    _assume_overlapping(image, bbox)

    x0, y0, x1, y1 = gateway._clamp_bbox(image, bbox)

    assert 0 <= x0 <= x1 <= image.width
    assert 0 <= y0 <= y1 <= image.height


@PROPS
@given(case=images_and_bboxes())
def test_crop_to_b64_jpeg_matches_clamped_box(gateway, case):
    width, height, bbox = case
    image = Image.new("RGB", (width, height), (40, 80, 120))
    _assume_overlapping(image, bbox)

    clamped = gateway._clamp_bbox(image, bbox)
    encoded = gateway._crop_to_b64_jpeg(image, bbox, quality=80)
    decoded = Image.open(io.BytesIO(base64.b64decode(encoded)))

    assert decoded.size == (clamped[2] - clamped[0], clamped[3] - clamped[1])


# --- 3. Degenerate crops ----------------------------------------------------


@st.composite
def crop_cases(draw):
    width = draw(st.integers(min_value=40, max_value=120))
    height = draw(st.integers(min_value=40, max_value=120))
    # Negative starts and past-the-image ends stay clipped into a valid (and
    # possibly zero-area) crop; an inverted crop is not a meaningful box.
    x0 = draw(st.integers(min_value=-40, max_value=width))
    y0 = draw(st.integers(min_value=-40, max_value=height))
    x1 = draw(st.integers(min_value=max(1, x0 + 1), max_value=width + 60))
    y1 = draw(st.integers(min_value=max(1, y0 + 1), max_value=height + 60))
    sigma = draw(st.integers(min_value=0, max_value=80))
    return width, height, (x0, y0, x1, y1), sigma


@PROPS
@given(case=crop_cases())
def test_is_degenerate_crop_matches_explicit_check(gateway, case):
    width, height, bbox, sigma = case
    if sigma:
        image = Image.effect_noise((width, height), sigma).convert("RGB")
    else:
        image = Image.new("RGB", (width, height), (30, 60, 90))

    x0, y0, x1, y1 = [round(v) for v in bbox]
    assume(x1 > 0 and y1 > 0)
    assume(x0 <= image.width and y0 <= image.height)
    if x1 - x0 < gateway.MIN_CROP_WIDTH or y1 - y0 < gateway.MIN_CROP_HEIGHT:
        expected = True
    else:
        clipped = image.crop(
            (max(x0, 0), max(y0, 0), min(x1, image.width), min(y1, image.height))
        )
        expected = (
            ImageStat.Stat(clipped.convert("L")).stddev[0] < gateway.MIN_CROP_STDDEV
        )

    assert gateway._is_degenerate_crop(image, bbox) is expected


# --- 4. Repetition guard ----------------------------------------------------


@PROPS
@given(chunk=st.text(alphabet="abcd", min_size=20, max_size=20))
def test_repetition_loop_fires_on_long_repeated_ngram(gateway, chunk):
    assert len(chunk) == gateway._REPEAT_NGRAM_LEN
    text = chunk * (gateway._REPEAT_NGRAM_THRESHOLD + 2)

    assert gateway._is_repetition_loop(text) is True


@PROPS
@given(word=st.text(alphabet="abcd", min_size=4, max_size=8))
def test_repetition_loop_fires_on_repeated_word_window(gateway, word):
    unit = " ".join([word] * gateway._REPEAT_WORD_WINDOW)
    assert gateway._is_repetition_loop(" ".join([unit] * gateway._REPEAT_WORD_TIMES))


def test_repetition_loop_ignores_legitimate_otsl_table(gateway):
    # Real table output from PaddleOCR-VL on sample_multipage_table.pdf. It
    # repeats phrases like "As a user, I would like to" many times, which the
    # old frequency heuristic wrongly classified as a loop.
    text = (
        "<fcel>Task #<fcel>Functional Requirement<fcel>Estimated Hours on Task<nl>"
        + "".join(
            f"<fcel>{i}<fcel>As a user, I would like to do task {i}<fcel>{i % 4 + 1}<nl>"
            for i in range(1, 17)
        )
    )
    assert gateway._is_repetition_loop(text) is False


@PROPS
@given(
    words=st.lists(
        st.sampled_from(["the", "cat", "sat", "mat", "and", "rat", "on"]),
        min_size=20,
        max_size=60,
    )
)
def test_repetition_loop_ignores_repeated_short_words(gateway, words):
    text = " ".join(words)
    assume(not gateway._is_repetition_loop(text))

    assert gateway._is_repetition_loop(text) is False


# --- 5. Structure payload ---------------------------------------------------

_FLAG_OVERRIDES = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useWiredTableCellsTransToHtml": True,
    "useWirelessTableCellsTransToHtml": True,
    "useE2eWiredTableRecModel": False,
    "useE2eWirelessTableRecModel": False,
}


@st.composite
def structure_requests(draw):
    keys = st.sampled_from(list(_FLAG_OVERRIDES) + ["useLayoutDetection", "custom"])
    extras = draw(st.dictionaries(keys, st.booleans(), max_size=6))
    file_type = draw(st.integers(min_value=0, max_value=3))
    return extras, file_type


@PROPS
@given(case=structure_requests())
def test_structure_payload_overwrites_caller_flags(gateway, case):
    extras, file_type = case
    assume(
        any(
            name in _FLAG_OVERRIDES and value is not _FLAG_OVERRIDES[name]
            for name, value in extras.items()
        )
    )
    req = gateway.LayoutParsingRequest(
        file=_noise_png(8, 8), fileType=file_type, **extras
    )

    payload = gateway._build_structure_payload(req)

    assert payload["file"] == req.file
    assert payload["fileType"] == 1
    for name, expected in _FLAG_OVERRIDES.items():
        assert payload[name] is expected


# --- 6. Direct VLM request bodies ------------------------------------------


@PROPS
@given(
    label=st.sampled_from(["table", "formula", "ocr"]),
    crop=st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
        min_size=1,
        max_size=32,
    ),
    max_tokens=st.integers(min_value=1, max_value=100000),
)
def test_direct_vlm_body_carries_prompt_image_and_generation_limits(
    gateway, monkeypatch, label, crop, max_tokens
):
    monkeypatch.setattr(gateway, "PIXEL_BUDGETS", None)
    monkeypatch.setattr(gateway, "VLM_MAX_COMPLETION_TOKENS", max_tokens)
    bodies = []

    async def handler(request):
        bodies.append(json.loads(request.content))
        return _chat()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    vlm = gateway._DirectVLMClient(client, model="test-model")
    try:
        result = asyncio.run(vlm.recognize(crop, label))
    finally:
        asyncio.run(client.aclose())

    assert result["text"] == "ok"
    assert len(bodies) == 1
    body = bodies[0]
    content = body["messages"][0]["content"]

    assert [part["type"] for part in content] == ["image_url", "text"]
    assert content[0]["image_url"]["url"] == f"data:image/jpeg;base64,{crop}"
    expected_prompt = {
        "table": "Table Recognition:",
        "formula": "Formula Recognition:",
        "ocr": "OCR:",
    }[label]
    assert content[1]["text"] == expected_prompt
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["max_completion_tokens"] == max_tokens
    assert "mm_processor_kwargs" not in json.dumps(body)


# --- 7. Semaphore backpressure ---------------------------------------------


@SMALL_PROPS
@given(cap=st.integers(min_value=1, max_value=4))
def test_concurrent_vlm_calls_never_exceed_semaphore(gateway, cap):
    blocks = [_block("text", order=i) for i in range(6)]
    active = 0
    peak = 0
    calls = 0

    async def handler(request):
        nonlocal active, peak, calls
        path = request.url.path
        if path.endswith("/layout-parsing"):
            return httpx.Response(200, json=_structure_body(blocks))
        if path.endswith("/chat/completions"):
            active += 1
            calls += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            return _chat()
        return httpx.Response(404)

    with TestClient(gateway.app) as client:
        gateway.app.state.vlm_sem = asyncio.Semaphore(cap)
        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway.app.state.httpx_client = transport_client
        gateway.app.state.vlm_client = gateway._DirectVLMClient(
            transport_client, model="test-model"
        )
        response = client.post(
            "/layout-parsing", json={"file": _noise_png(), "fileType": 1}
        )

    assert response.status_code == 200
    assert calls == len(blocks)
    assert 1 <= peak <= cap
