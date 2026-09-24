#!/usr/bin/env python3

import base64
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

import requests
from fastapi import FastAPI, HTTPException
from PIL import Image, ImageStat
from pydantic import BaseModel, ConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hybrid_gateway")

STRUCTURE_GATEWAY_URL = os.environ.get(
    "STRUCTURE_GATEWAY_URL", "http://paddlex-pipeline:8080/layout-parsing"
)
VL_GATEWAY_URL = os.environ.get(
    "VL_GATEWAY_URL", "http://paddleocr-vl-api:8080/layout-parsing"
)
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))
VLM_MAX_WORKERS = int(os.environ.get("VLM_MAX_WORKERS", "16"))

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

# Picture-like blocks, which are never sent to PaddleOCR-VL
IMAGE_LABELS = {"image", "chart", "seal", "figure", "header_image", "footer_image"}

PROMPT_LABEL_BY_BLOCK = {"table": "table", "formula": "formula"}

DEFAULT_PROMPT_LABEL = "ocr"

# Quality for the extracted figure JPEGs returned in markdown_images.
IMAGE_JPEG_QUALITY = int(os.environ.get("IMAGE_JPEG_QUALITY", "95"))


MIN_CROP_WIDTH = int(os.environ.get("MIN_CROP_WIDTH", "20"))
MIN_CROP_HEIGHT = int(os.environ.get("MIN_CROP_HEIGHT", "20"))
MIN_CROP_STDDEV = float(os.environ.get("MIN_CROP_STDDEV", "4.0"))

app = FastAPI(title="PP-StructureV3 + PaddleOCR-VL hybrid gateway")


class LayoutParsingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str
    fileType: int = 1
    # Any additional PP-StructureV3 request fields are forwarded as-is


def _crop_to_b64_png(image: Image.Image, bbox) -> str:
    x0, y0, x1, y1 = [round(v) for v in bbox]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, image.width), min(y1, image.height)
    crop = image.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
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


def _recognize_with_vlm(
    crop_b64: str, prompt_label: str = DEFAULT_PROMPT_LABEL
) -> str | None:
    """Sends one cropped element to PaddleOCR-VL and returns its recognized text."""
    payload = {
        "file": crop_b64,
        "fileType": 1,
        "useLayoutDetection": False,
        "promptLabel": prompt_label,
    }
    try:
        resp = requests.post(VL_GATEWAY_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        pages = body["result"]["layoutParsingResults"]
        blocks = pages[0]["prunedResult"].get("parsing_res_list", [])
        if not blocks:
            return None
        return blocks[0].get("block_content")
    except Exception:
        logger.exception(
            "VLM recognition failed for a crop; keeping PP-StructureV3 text"
        )
        return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/layout-parsing")
def layout_parsing(req: LayoutParsingRequest):
    if req.fileType != 1:
        raise HTTPException(
            status_code=400,
            detail="hybrid_gateway only supports single images (fileType=1); "
            "split PDF pages into images before calling it.",
        )

    extra_fields = req.model_dump(exclude={"file", "fileType"})
    structure_payload = {
        "file": req.file,
        "fileType": 1,
        **extra_fields,
        # Always force cell-level detection so table geometry is available to merge.
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
    resp = requests.post(
        STRUCTURE_GATEWAY_URL, json=structure_payload, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    body = resp.json()

    image_bytes = base64.b64decode(req.file)
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    page = body["result"]["layoutParsingResults"][0]
    pruned = page["prunedResult"]
    blocks = [
        b
        for b in pruned.get("parsing_res_list", [])
        if b.get("block_label") in (RECOGNIZABLE_LABELS - IMAGE_LABELS)
        and not _is_degenerate_crop(image, b["block_bbox"])
    ]

    def _job(block):
        crop_b64 = _crop_to_b64_png(image, block["block_bbox"])
        prompt_label = PROMPT_LABEL_BY_BLOCK.get(
            block.get("block_label"), DEFAULT_PROMPT_LABEL
        )
        return block, _recognize_with_vlm(crop_b64, prompt_label)

    with ThreadPoolExecutor(max_workers=VLM_MAX_WORKERS) as pool:
        futures = [pool.submit(_job, block) for block in blocks]
        for future in as_completed(futures):
            block, vlm_text = future.result()
            if vlm_text:
                block["block_content"] = vlm_text

    # Assemble markdown in layout order. Picture blocks become an image
    # reference whose bytes are returned alongside in markdown_images; every
    # other block contributes its (VLM-substituted) text. Blocks are filtered by
    # label rather than by "has content", so PP-OCR's noise over a figure can no
    # longer reach the output.
    markdown_images: dict[str, str] = {}
    parts: list[str] = []
    for b in pruned.get("parsing_res_list", []):
        if b.get("block_label") in IMAGE_LABELS:
            figure = _extract_figure(image, b["block_bbox"])
            if figure is None:
                continue
            ref, data = figure
            markdown_images[ref] = data
            # Record the reference on the block and drop the OCR noise, so a
            # caller reading parsing_res_list directly sees the same thing the
            # markdown does rather than the hallucinated text.
            b["block_image"] = ref
            b["block_content"] = ""
            parts.append(f"![]({ref})")
        elif b.get("block_content"):
            content = b["block_content"]
            if b.get("block_label") == "table":
                # block_content keeps the HTML (it is the faithful structured
                # form for anyone reading parsing_res_list); only the markdown
                # rendering is converted to a pipe table.
                content = _table_html_to_markdown(content) or content
            parts.append(content)

    page["markdown"] = {"text": "\n\n".join(parts), "markdown_images": markdown_images}

    return body


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
