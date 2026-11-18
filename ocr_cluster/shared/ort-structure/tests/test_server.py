"""Section 5.1 (HTTP layer) -- server boundary behavior, no model load.

Covers: Bearer auth (accept the key, reject others), /health shallow without
model load, /ready 200 when loaded and 503 when the load fails, 400 on a bad
image / wrong fileType, 503 + Retry-After when the structure slot is saturated,
504 on a per-request timeout, no crop-buffer sharing across concurrent
requests, and that timeouts/saturation free the semaphore.

A stub Pipeline stands in for the real one, so nothing here loads a model or
needs a GPU.
"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ort_structure import server as srv  # noqa: E402


def _b64_png(size, color):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


BIG = _b64_png((640, 480), (10, 120, 220))
SMALL = _b64_png((320, 240), (200, 20, 20))
BAD = base64.b64encode(b"not an image at all").decode("ascii")


class StubPipeline:
    """A drop-in for Pipeline: records concurrency, honours a delay, and echoes
    the input image's dimensions in the prunedResult (so buffer sharing is
    observable)."""

    def __init__(self, delay=0.0, slow_first=0.0, fail=None):
        self.delay = delay
        self.slow_first = slow_first
        self.fail = fail
        self.max_concurrent = 0
        self.calls = 0
        self._cur = 0
        self._lock = threading.Lock()

    def process_page(self, arr, wall_time=None):
        with self._lock:
            self._cur += 1
            self.max_concurrent = max(self.max_concurrent, self._cur)
            first = self.calls == 0
            self.calls += 1
        if self.slow_first and first:
            time.sleep(self.slow_first)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            with self._lock:
                self._cur -= 1
            raise self.fail
        h, w = arr.shape[:2]
        pruned = {
            "width": w, "height": h, "model_settings": {},
            "parsing_res_list": [], "layout_det_res": {"boxes": []},
            "overall_ocr_res": {"dt_polys": []}, "table_res_list": [],
        }
        with self._lock:
            self._cur -= 1
        return pruned, 0.1

    def envelope(self, pruned):
        return {"result": {"layoutParsingResults": [{"prunedResult": pruned}]}}


def _app(**kw):
    kw.setdefault("api_key", "testkey")
    return srv.create_app(**kw)


# ---------------------------------------------------------------------------
# simple (synchronous) boundary behavior via TestClient
# ---------------------------------------------------------------------------
def test_health_is_shallow_and_needs_no_model():
    app = _app(pipeline=StubPipeline())
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_ready_200_when_loaded():
    app = _app(pipeline=StubPipeline())
    with TestClient(app) as c:
        assert c.get("/ready").status_code == 200


def test_ready_503_when_load_fails(monkeypatch):
    def boom(**kw):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(srv, "Pipeline", boom)
    app = srv.create_app(pipeline=None)  # startup load will raise
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # liveness is independent
        assert c.get("/ready").status_code == 503


def test_auth_accepts_key_and_rejects_others():
    app = _app(pipeline=StubPipeline())
    with TestClient(app) as c:
        assert c.post("/layout-parsing", json={"file": SMALL}).status_code == 401  # no key
        assert c.post(
            "/layout-parsing", json={"file": SMALL},
            headers={"Authorization": "Bearer wrongkey"},
        ).status_code == 401
        r = c.post(
            "/layout-parsing", json={"file": SMALL},
            headers={"Authorization": "Bearer testkey"},
        )
        assert r.status_code == 200


def test_bad_image_is_400():
    app = _app(pipeline=StubPipeline())
    with TestClient(app) as c:
        r = c.post(
            "/layout-parsing", json={"file": BAD},
            headers={"Authorization": "Bearer testkey"},
        )
    assert r.status_code == 400


def test_wrong_filetype_is_400():
    app = _app(pipeline=StubPipeline())
    with TestClient(app) as c:
        r = c.post(
            "/layout-parsing", json={"file": SMALL, "fileType": 2},
            headers={"Authorization": "Bearer testkey"},
        )
    assert r.status_code == 400


def test_timeout_is_504_and_frees_slot():
    # The first call is slow (exceeds request_timeout -> 504); the slot is
    # released on the timeout, so the second call (fast) succeeds.
    app = _app(pipeline=StubPipeline(slow_first=0.5), max_inflight=1,
               queue_timeout=5.0, request_timeout=0.05)
    with TestClient(app) as c:
        h = {"Authorization": "Bearer testkey"}
        r = c.post("/layout-parsing", json={"file": SMALL}, headers=h)
        assert r.status_code == 504
        # the slot was released by the timeout, so the next request succeeds
        r2 = c.post("/layout-parsing", json={"file": SMALL}, headers=h)
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# concurrent behavior via an asyncio + httpx ASGITransport harness
# ---------------------------------------------------------------------------
def _run(coro):
    return asyncio.run(coro)


def test_saturation_is_503_with_retry_after_and_frees_slot():
    async def scenario():
        stub = StubPipeline(delay=0.4)
        app = _app(pipeline=stub, max_inflight=1, queue_timeout=0.1, request_timeout=5.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            h = {"Authorization": "Bearer testkey"}
            a = asyncio.create_task(c.post("/layout-parsing", json={"file": BIG}, headers=h))
            await asyncio.sleep(0.15)  # A is now holding the only slot
            b = await c.post("/layout-parsing", json={"file": SMALL}, headers=h)
            assert b.status_code == 503, b.text
            assert b.headers.get("retry-after")
            ra = await a
            assert ra.status_code == 200
            # slot freed -> a third request now succeeds
            c3 = await c.post("/layout-parsing", json={"file": SMALL}, headers=h)
            assert c3.status_code == 200
        return stub

    stub = _run(scenario())
    assert stub.max_concurrent == 1  # the semaphore capped in-flight at 1


def test_no_shared_crop_buffers_across_concurrent_requests():
    async def scenario():
        stub = StubPipeline(delay=0.1)
        app = _app(pipeline=stub, max_inflight=4, queue_timeout=5.0, request_timeout=5.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            h = {"Authorization": "Bearer testkey"}
            big, small = await asyncio.gather(
                c.post("/layout-parsing", json={"file": BIG}, headers=h),
                c.post("/layout-parsing", json={"file": SMALL}, headers=h),
            )
        return big, small

    big, small = _run(scenario())
    # Each response must reflect its own input image's dimensions (no sharing).
    assert big.json()["result"]["layoutParsingResults"][0]["prunedResult"]["width"] == 640
    assert small.json()["result"]["layoutParsingResults"][0]["prunedResult"]["width"] == 320
