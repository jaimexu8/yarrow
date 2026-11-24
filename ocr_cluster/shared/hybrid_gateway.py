#!/usr/bin/env python3
"""Orchestration gateway for layout parsing and per-block VLM recognition.

One async uvicorn process handles every /layout-parsing request. A bounded
structure call supplies the layout boxes, then each text-bearing crop is sent
to an OpenAI-compatible VLM chat endpoint under a semaphore. Image work runs
off the event loop, and the whole request has a deadline."""

import asyncio
import base64
import io
import json
import logging
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageStat
from pydantic import BaseModel, ConfigDict

try:  # repo checkout: ocr_cluster is a namespace package on sys.path
    from ocr_cluster.shared.otsl import convert_otsl_to_html
except ModuleNotFoundError:  # gateway image: otsl.py sits next to this file
    # Tests import this file by path from backend/, where the shared directory
    # is not on sys.path. Add it so the flat import works there too.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from otsl import convert_otsl_to_html

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hybrid_gateway")

STRUCTURE_GATEWAY_URL = os.environ.get(
    "STRUCTURE_GATEWAY_URL", "http://ort-structure:8080/layout-parsing"
)
# Readiness must probe the structure service's own /ready endpoint. GET on
# /layout-parsing returns 405, which is a responding service but logs noise on
# every healthcheck.
_structure_parts = urlsplit(STRUCTURE_GATEWAY_URL)
STRUCTURE_READY_URL = urlunsplit(
    (_structure_parts.scheme, _structure_parts.netloc, "/ready", "", "")
)
# The structure service (ort-structure) requires Bearer auth on /layout-parsing
# and rejects every request that does not carry it. The gateway sends the same
# key the ort-structure container validates against. When it is unset the
# header is omitted and the service answers 401: it never runs auth-off, so an
# unset key is a visible failure rather than a silent open endpoint.
STRUCTURE_API_KEY = os.environ.get("STRUCTURE_API_KEY", "").strip()
VLM_URL = os.environ.get("VLM_URL", "http://vlm-server:8000/v1").rstrip("/")
VLM_MODEL = os.environ.get("VLM_MODEL", "").strip()
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))
VLM_MAX_INFLIGHT = int(os.environ.get("VLM_MAX_INFLIGHT", "16"))
VLM_MAX_COMPLETION_TOKENS = int(
    os.environ.get("VLM_MAX_COMPLETION_TOKENS", "8192")
)
VLM_JPEG_QUALITY = int(os.environ.get("VLM_JPEG_QUALITY", "75"))
STRUCTURE_MAX_INFLIGHT = int(os.environ.get("STRUCTURE_MAX_INFLIGHT", "2"))
STRUCTURE_QUEUE_TIMEOUT = float(os.environ.get("STRUCTURE_QUEUE_TIMEOUT", "120"))
GATEWAY_DEADLINE = float(os.environ.get("GATEWAY_DEADLINE", "480"))

MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()
MODEL_VERSION = os.environ.get("MODEL_VERSION", "").strip()
if not MODEL_NAME or not MODEL_VERSION:
    raise RuntimeError("MODEL_NAME and MODEL_VERSION must be set")


def _load_pixel_budgets():
    raw = os.environ.get("VLM_PIXEL_BUDGETS", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("invalid VLM_PIXEL_BUDGETS; sending no mm_processor_kwargs")
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed


PIXEL_BUDGETS = _load_pixel_budgets()

# Layout labels worth re-recognizing with the VLM.
RECOGNIZABLE_LABELS = {
    "text",
    "table",
    "formula",
    "paragraph_title",
    "doc_title",
    "abstract",
    "content",
}

# Picture-like blocks, which are never sent to the VLM.
IMAGE_LABELS = {"image", "chart", "seal", "figure", "header_image", "footer_image"}

PROMPT_LABEL_BY_BLOCK = {"table": "table", "formula": "formula"}
DEFAULT_PROMPT_LABEL = "ocr"
PROMPT_TEXT_BY_LABEL = {
    "ocr": "OCR:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
}

# Quality for the extracted figure JPEGs returned in markdown_images.
IMAGE_JPEG_QUALITY = int(os.environ.get("IMAGE_JPEG_QUALITY", "95"))

MIN_CROP_WIDTH = int(os.environ.get("MIN_CROP_WIDTH", "20"))
MIN_CROP_HEIGHT = int(os.environ.get("MIN_CROP_HEIGHT", "20"))
MIN_CROP_STDDEV = float(os.environ.get("MIN_CROP_STDDEV", "4.0"))

_EXECUTOR_WORKERS = min(8, os.cpu_count() or 1)
_RETRY_DELAY = 0.2
_MODEL_RETRY_DELAY = 0.2
_REPEAT_NGRAM_LEN = 20
_REPEAT_NGRAM_THRESHOLD = 4
_REPEAT_WORD_WINDOW = 8
_REPEAT_WORD_TIMES = 3
_RETRY_AFTER_SECONDS = 2
_CONNECT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


def _crop_to_b64_jpeg(image: Image.Image, bbox, quality=VLM_JPEG_QUALITY) -> str:
    x0, y0, x1, y1 = [round(v) for v in bbox]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, image.width), min(y1, image.height)
    crop = image.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _TableHTMLParser(HTMLParser):
    """Minimal <table> reader -- stdlib only, so the gateway stays dependency-light."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.has_span = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            for span in ("colspan", "rowspan"):
                try:
                    if int(attrs.get(span, 1)) > 1:
                        self.has_span = True
                except (TypeError, ValueError):
                    self.has_span = True
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if self._row is None:
                self._row = []
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def close(self):
        super().close()
        if self._row:
            self.rows.append(self._row)
            self._row = None


def _table_html_to_markdown(html: str) -> str | None:
    """Converts a simple HTML table to a GFM pipe table.

    Returns None when the table cannot be faithfully represented -- notably when
    it uses rowspan/colspan, which GFM has no syntax for. The caller then keeps
    the HTML, which markdown renderers pass through unchanged; a silently
    flattened merged cell would be worse than a table that renders as HTML.
    """
    if "<table" not in html.lower():
        return None
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, IndexError, AttributeError, TypeError):
        logger.warning("could not parse VLM table HTML; leaving it as-is")
        return None
    rows = [r for r in parser.rows if any(c for c in r)]
    if not rows or parser.has_span:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    def _row(cells):
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    return "\n".join(
        [
            _row(rows[0]),
            "| " + " | ".join(["---"] * width) + " |",
            *(_row(r) for r in rows[1:]),
        ]
    )


def _clamp_bbox(image: Image.Image, bbox):
    x0, y0, x1, y1 = [round(v) for v in bbox]
    return max(x0, 0), max(y0, 0), min(x1, image.width), min(y1, image.height)


def _extract_figure(image: Image.Image, bbox) -> tuple[str, str] | None:
    """Crops a picture block and returns (markdown_ref, base64_jpeg)"""
    x0, y0, x1, y1 = _clamp_bbox(image, bbox)
    if x1 - x0 < MIN_CROP_WIDTH or y1 - y0 < MIN_CROP_HEIGHT:
        return None
    # Deliberately no stddev check here, unlike text blocks: a flat crop is a
    # hallucination risk only for the VLM, and a solid-colour logo or a plain
    # chart background is a legitimate figure worth returning.
    buf = io.BytesIO()
    image.crop((x0, y0, x1, y1)).save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    ref = f"imgs/img_in_image_box_{x0}_{y0}_{x1}_{y1}.jpg"
    return ref, base64.b64encode(buf.getvalue()).decode("ascii")


def _is_degenerate_crop(image: Image.Image, bbox) -> bool:
    """True if bbox is too small or the crop is near-blank (flat pixel stddev)."""
    x0, y0, x1, y1 = [round(v) for v in bbox]
    width, height = x1 - x0, y1 - y0
    if width < MIN_CROP_WIDTH or height < MIN_CROP_HEIGHT:
        return True
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, image.width), min(y1, image.height)
    stat = ImageStat.Stat(image.crop((x0, y0, x1, y1)).convert("L"))
    return stat.stddev[0] < MIN_CROP_STDDEV


def _decode_image(file_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(file_b64))).convert("RGB")


def _select_blocks(image: Image.Image, pruned):
    return [
        b
        for b in pruned.get("parsing_res_list", [])
        if b.get("block_label") in (RECOGNIZABLE_LABELS - IMAGE_LABELS)
        and not _is_degenerate_crop(image, b["block_bbox"])
    ]


def _priority(label) -> int:
    if label == "table":
        return 0
    if label == "formula":
        return 1
    return 2


def _prepare_blocks_sync(image: Image.Image, selected):
    prepared = []
    for block in selected:
        prompt_label = PROMPT_LABEL_BY_BLOCK.get(
            block.get("block_label"), DEFAULT_PROMPT_LABEL
        )
        crop_b64 = _crop_to_b64_jpeg(image, block["block_bbox"])
        prepared.append((block, prompt_label, crop_b64))
    prepared.sort(key=lambda item: _priority(item[0].get("block_label")))
    return prepared


def _assemble_markdown(image: Image.Image, pruned) -> dict:
    """Build the markdown section, mutating figure blocks in place."""
    markdown_images: dict[str, str] = {}
    parts: list[str] = []
    for b in pruned.get("parsing_res_list", []):
        if b.get("block_label") in IMAGE_LABELS:
            figure = _extract_figure(image, b["block_bbox"])
            if figure is None:
                continue
            ref, data = figure
            markdown_images[ref] = data
            b["block_image"] = ref
            b["block_content"] = ""
            parts.append(f"![]({ref})")
        elif b.get("block_content"):
            content = b["block_content"]
            if b.get("block_label") == "table":
                content = _table_html_to_markdown(content) or content
            parts.append(content)
    return {"text": "\n\n".join(parts), "markdown_images": markdown_images}


def _build_structure_payload(req) -> dict:
    extra_fields = req.model_dump(exclude={"file", "fileType"})
    return {
        "file": req.file,
        "fileType": 1,
        **extra_fields,
        "useWiredTableCellsTransToHtml": True,
        "useWirelessTableCellsTransToHtml": True,
        "useE2eWiredTableRecModel": False,
        "useE2eWirelessTableRecModel": False,
        # Must stay off: block_bbox/cell boxes are returned in the *processed*
        # image's coordinate space. Cropping below uses the raw bytes we were
        # given, so any orientation/unwarping here would desync every bbox
        # from the actual pixels, even when the reported correction angle is 0.
        # Placed after extra_fields so a caller can't accidentally re-enable it.
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
    }


def _structure_headers() -> dict:
    if STRUCTURE_API_KEY:
        return {"Authorization": f"Bearer {STRUCTURE_API_KEY}"}
    return {}


def _is_structure_only(value: str | None) -> bool:
    """Whether the caller asked for layout results without VLM recognition."""
    return (value or "").strip().lower() in ("true", "1", "yes")


def _is_repetition_loop(text: str) -> bool:
    """Detect a model loop without punishing legitimate repeated content.

    Tables contain OTSL tags and phrases like "As a user, I would like to" many
    times, so counting repeated n-grams fires on valid output. A loop repeats a
    block back to back. Check contiguous word windows and a repeated character
    suffix instead, after stripping markup tags.
    """
    if not text:
        return False
    plain = re.sub(r"<[^>]*>", " ", text)
    words = plain.split()
    needed = _REPEAT_WORD_WINDOW * _REPEAT_WORD_TIMES
    if len(words) >= needed:
        for start in range(len(words) - needed + 1):
            unit = words[start : start + _REPEAT_WORD_WINDOW]
            if (
                words[start + _REPEAT_WORD_WINDOW : start + 2 * _REPEAT_WORD_WINDOW]
                == unit
                and words[start + 2 * _REPEAT_WORD_WINDOW : start + needed] == unit
            ):
                return True
    compact = "".join(plain.split())
    if len(compact) >= _REPEAT_NGRAM_LEN * _REPEAT_NGRAM_THRESHOLD:
        max_unit = min(200, len(compact) // _REPEAT_NGRAM_THRESHOLD)
        for size in range(_REPEAT_NGRAM_LEN, max_unit + 1):
            unit = compact[-size:]
            if unit * _REPEAT_NGRAM_THRESHOLD == compact[-size * _REPEAT_NGRAM_THRESHOLD :]:
                return True
    return False


class _DirectVLMClient:
    """Small OpenAI-compatible chat client with discovery, retries and guards."""

    def __init__(self, client: httpx.AsyncClient, model: str | None = None):
        self.client = client
        self.model = model
        self._lock = asyncio.Lock()

    async def resolve_model(self) -> str:
        if self.model:
            return self.model
        async with self._lock:
            if self.model:
                return self.model
            for attempt in range(2):
                try:
                    resp = await self.client.get(f"{VLM_URL}/models")
                    resp.raise_for_status()
                    ids = [
                        m["id"] for m in resp.json().get("data", []) if m.get("id")
                    ]
                    if not ids:
                        raise ValueError("VLM server returned no model ids")
                    self.model = ids[0]
                    return self.model
                except Exception:
                    if attempt == 0:
                        await asyncio.sleep(_MODEL_RETRY_DELAY)
                        continue
                    logger.exception("vlm model discovery failed")
                    raise

    async def recognize(self, crop_b64: str, prompt_label: str):
        try:
            model = await self.resolve_model()
        except Exception:  # noqa: BLE001  # any discovery failure falls back to structure text
            return None
        prompt = PROMPT_TEXT_BY_LABEL.get(prompt_label, "OCR:")
        extra_body = {"skip_special_tokens": True}
        if PIXEL_BUDGETS:
            extra_body["mm_processor_kwargs"] = PIXEL_BUDGETS
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{crop_b64}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0,
            "max_completion_tokens": VLM_MAX_COMPLETION_TOKENS,
            "extra_body": extra_body,
        }
        retries = 0
        url = f"{VLM_URL}/chat/completions"
        while True:
            try:
                resp = await self.client.post(url, json=body)
                if resp.status_code in (429, 503) and retries < 1:
                    retries += 1
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                if resp.status_code >= 400:
                    logger.warning(
                        "vlm_http_error status=%s label=%s", resp.status_code, prompt_label
                    )
                    return None
                choices = (resp.json() or {}).get("choices") or []
                if not choices:
                    return None
                choice = choices[0]
                text = (choice.get("message") or {}).get("content") or ""
                finish_reason = choice.get("finish_reason")
                if _is_repetition_loop(text):
                    logger.warning("vlm_repetition_loop label=%s", prompt_label)
                    return {"loop": True, "finish_reason": finish_reason}
                return {"text": text, "finish_reason": finish_reason}
            except _CONNECT_ERRORS:
                if retries < 1:
                    retries += 1
                    continue
                logger.warning("vlm_connection_error label=%s", prompt_label)
                return None
            except Exception:
                logger.exception("vlm_call_failed label=%s", prompt_label)
                return None


@dataclass
class _RequestContext:
    request_id: str
    start: float = field(default_factory=time.perf_counter)
    width: int = 0
    height: int = 0
    structure_wait_ms: float = 0.0
    structure_ms: float = 0.0
    crop_ms: float = 0.0
    vlm_ms: float = 0.0
    per_label_counts: dict = field(default_factory=dict)
    per_label_latency: dict = field(default_factory=dict)
    finish_reasons: dict = field(default_factory=dict)
    structure_queue_samples: list = field(default_factory=list)
    vlm_queue_samples: list = field(default_factory=list)
    vlm_inflight_samples: list = field(default_factory=list)
    failed_blocks: int = 0
    truncated_blocks: int = 0
    loop_blocks: int = 0
    vlm_active: int = 0
    tasks: list = field(default_factory=list)
    logged: bool = False

    def total_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000


def _ensure_runtime(app: FastAPI):
    if not hasattr(app.state, "httpx_client"):
        limits = httpx.Limits(
            max_connections=VLM_MAX_INFLIGHT + 8,
            max_keepalive_connections=VLM_MAX_INFLIGHT + 8,
        )
        app.state.httpx_client = httpx.AsyncClient(
            limits=limits, timeout=httpx.Timeout(REQUEST_TIMEOUT)
        )
    if not hasattr(app.state, "structure_sem"):
        app.state.structure_sem = asyncio.Semaphore(STRUCTURE_MAX_INFLIGHT)
    if not hasattr(app.state, "vlm_sem"):
        app.state.vlm_sem = asyncio.Semaphore(VLM_MAX_INFLIGHT)
    if not hasattr(app.state, "vlm_client"):
        app.state.vlm_client = _DirectVLMClient(app.state.httpx_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    executor = ThreadPoolExecutor(max_workers=_EXECUTOR_WORKERS)
    asyncio.get_running_loop().set_default_executor(executor)
    limits = httpx.Limits(
        max_connections=VLM_MAX_INFLIGHT + 8,
        max_keepalive_connections=VLM_MAX_INFLIGHT + 8,
    )
    app.state.httpx_client = httpx.AsyncClient(
        limits=limits, timeout=httpx.Timeout(REQUEST_TIMEOUT)
    )
    app.state.structure_sem = asyncio.Semaphore(STRUCTURE_MAX_INFLIGHT)
    app.state.vlm_sem = asyncio.Semaphore(VLM_MAX_INFLIGHT)
    app.state.vlm_client = _DirectVLMClient(app.state.httpx_client)
    try:
        yield
    finally:
        await app.state.httpx_client.aclose()
        executor.shutdown(wait=False)


app = FastAPI(
    title="PP-StructureV3 + PaddleOCR-VL hybrid gateway", lifespan=lifespan
)


class LayoutParsingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str
    fileType: int = 1
    # Any additional PP-StructureV3 request fields are forwarded as-is


@app.middleware("http")
async def model_identity_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/layout-parsing":
        response.headers["X-Yarrow-Model-Name"] = MODEL_NAME
        response.headers["X-Yarrow-Model-Version"] = MODEL_VERSION
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request):
    _ensure_runtime(request.app)
    client = request.app.state.httpx_client
    try:
        resp = await client.get(STRUCTURE_READY_URL, timeout=5)
        if resp.status_code >= 500:
            raise RuntimeError(f"structure upstream status {resp.status_code}")
    except Exception:  # noqa: BLE001  # readiness maps any upstream failure to 503
        logger.warning("readiness check failed for structure upstream")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "upstream": "structure"},
        )
    try:
        resp = await client.get(f"{VLM_URL}/models", timeout=5)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001  # readiness maps any upstream failure to 503
        logger.warning("readiness check failed for VLM upstream")
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "upstream": "vlm"}
        )
    return {"status": "ready"}


async def _structure_call(app: FastAPI, payload, headers, ctx):
    sem = app.state.structure_sem
    start = time.perf_counter()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=STRUCTURE_QUEUE_TIMEOUT)
    except TimeoutError:
        ctx.structure_wait_ms = (time.perf_counter() - start) * 1000
        return None, "timeout"
    ctx.structure_wait_ms = (time.perf_counter() - start) * 1000
    ctx.structure_queue_samples.append(max(0, getattr(sem, "_value", 0)))
    try:
        resp = await app.state.httpx_client.post(
            STRUCTURE_GATEWAY_URL, json=payload, headers=headers
        )
        resp.raise_for_status()
        return resp.json(), None
    except Exception:
        logger.exception("structure_call_failed")
        return None, "error"
    finally:
        sem.release()


async def _vlm_recognize_one(app, ctx, block, prompt_label, crop_b64):
    queued = time.perf_counter()
    async with app.state.vlm_sem:
        ctx.vlm_queue_samples.append((time.perf_counter() - queued) * 1000)
        ctx.vlm_active += 1
        ctx.vlm_inflight_samples.append(ctx.vlm_active)
        start = time.perf_counter()
        try:
            result = await app.state.vlm_client.recognize(crop_b64, prompt_label)
            return block, prompt_label, result
        finally:
            latency = (time.perf_counter() - start) * 1000
            ctx.vlm_active -= 1
            label = block.get("block_label", "unknown")
            series = ctx.per_label_latency.setdefault(label, [])
            series.append(latency)


def _apply_vlm_result(block, result, ctx):
    label = block.get("block_label", "unknown")
    block["vlm_truncated"] = False
    block["vlm_failed"] = False
    if result and result.get("finish_reason"):
        reasons = ctx.finish_reasons.setdefault(label, {})
        reason = result["finish_reason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    if result and result.get("finish_reason") == "length":
        block["vlm_truncated"] = True
        ctx.truncated_blocks += 1
    if result is None or result.get("loop"):
        block["vlm_failed"] = True
        ctx.failed_blocks += 1
        if result and result.get("loop"):
            ctx.loop_blocks += 1
        return
    text = result.get("text") or ""
    if text.strip():
        block["block_content"] = text
    else:
        block["vlm_failed"] = True
        ctx.failed_blocks += 1


def _normalize_direct_table_content(text: str) -> str:
    """Convert OTSL table markup returned by the VLM into HTML.

    The gateway calls the VLM chat API itself, so table regions use OTSL v1.0
    tags that PaddleX would normally convert. Leave existing HTML alone and
    keep the original text if the converter finds no OTSL cells.
    """
    if not text or not text.strip():
        return text
    if text.lstrip().lower().startswith("<table"):
        return text
    return convert_otsl_to_html(text) or text


async def _run_direct(req: LayoutParsingRequest, request: Request, ctx):
    app = request.app
    _ensure_runtime(app)
    structure_only = _is_structure_only(
        request.headers.get("x-yarrow-structure-only")
    )
    start = time.perf_counter()
    body, err = await _structure_call(
        app, _build_structure_payload(req), _structure_headers(), ctx
    )
    ctx.structure_ms = (time.perf_counter() - start) * 1000
    if err == "timeout":
        return JSONResponse(
            status_code=503,
            content={"detail": "structure queue timeout"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )
    if err:
        return JSONResponse(
            status_code=502, content={"detail": "structure service unavailable"}
        )

    try:
        page = body["result"]["layoutParsingResults"][0]
        pruned = page["prunedResult"]
    except (KeyError, IndexError, TypeError):
        return JSONResponse(
            status_code=502, content={"detail": "malformed structure response"}
        )

    image = await asyncio.to_thread(_decode_image, req.file)
    ctx.width, ctx.height = image.size
    if structure_only:
        page["markdown"] = {"text": "", "markdown_images": {}}
        _log_request(ctx)
        return body

    selected = await asyncio.to_thread(_select_blocks, image, pruned)

    crop_start = time.perf_counter()
    prepared = await asyncio.to_thread(_prepare_blocks_sync, image, selected)
    ctx.crop_ms = (time.perf_counter() - crop_start) * 1000

    for block, prompt_label, _ in prepared:
        label = block.get("block_label", "unknown")
        ctx.per_label_counts[label] = ctx.per_label_counts.get(label, 0) + 1

    for block, prompt_label, crop_b64 in prepared:
        task = asyncio.create_task(
            _vlm_recognize_one(app, ctx, block, prompt_label, crop_b64)
        )
        ctx.tasks.append(task)

    vlm_start = time.perf_counter()
    if ctx.tasks:
        results = await asyncio.gather(*ctx.tasks)
        for block, prompt_label, result in results:
            _apply_vlm_result(block, result, ctx)
    ctx.vlm_ms = (time.perf_counter() - vlm_start) * 1000

    # Convert OTSL table regions to HTML before markdown assembly sees them.
    for block in pruned.get("parsing_res_list", []):
        if block.get("block_label") == "table":
            block["block_content"] = _normalize_direct_table_content(
                block.get("block_content") or ""
            )

    markdown = await asyncio.to_thread(_assemble_markdown, image, pruned)
    page["markdown"] = markdown
    _log_request(ctx)
    return body


def _log_request(ctx: _RequestContext):
    if ctx.logged:
        return
    ctx.logged = True
    latencies = {
        label: round(sum(values) / len(values), 1)
        for label, values in ctx.per_label_latency.items()
        if values
    }
    record = {
        "request_id": ctx.request_id,
        "page": [ctx.width, ctx.height],
        "structure_wait_ms": round(ctx.structure_wait_ms, 1),
        "structure_ms": round(ctx.structure_ms, 1),
        "crop_ms": round(ctx.crop_ms, 1),
        "vlm_ms": round(ctx.vlm_ms, 1),
        "blocks_by_label": ctx.per_label_counts,
        "failed_blocks": ctx.failed_blocks,
        "truncated_blocks": ctx.truncated_blocks,
        "loop_blocks": ctx.loop_blocks,
        "total_ms": round(ctx.total_ms(), 1),
        "structure_queue_depth": ctx.structure_queue_samples,
        "vlm_queue_wait_ms": [round(v, 1) for v in ctx.vlm_queue_samples],
        "vlm_inflight": ctx.vlm_inflight_samples,
        "vlm_latency_ms": latencies,
        "finish_reasons": ctx.finish_reasons,
    }
    logger.info("layout_parsing_request %s", json.dumps(record, sort_keys=True))


@app.post("/layout-parsing")
async def layout_parsing(req: LayoutParsingRequest, request: Request):
    if req.fileType != 1:
        raise HTTPException(
            status_code=400,
            detail="hybrid_gateway only supports single images (fileType=1); "
            "split PDF pages into images before calling it.",
        )

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    ctx = _RequestContext(request_id=request_id)
    try:
        async with asyncio.timeout(GATEWAY_DEADLINE):
            return await _run_direct(req, request, ctx)
    except TimeoutError:
        for task in ctx.tasks:
            task.cancel()
        if ctx.tasks:
            await asyncio.gather(*ctx.tasks, return_exceptions=True)
        logger.warning("gateway_deadline_exceeded request_id=%s", request_id)
        return JSONResponse(
            status_code=504, content={"detail": "gateway deadline exceeded"}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
