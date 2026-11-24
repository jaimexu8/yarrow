"""PDF text-layer extraction, its quality gate and word-to-layout merging.

The extractor reads the words PyMuPDF already found in a PDF. A page that
passes the gate can skip OCR; its words are then mapped onto the layout
regions returned by the inference gateway.
"""

import copy
import html
import math
import statistics
import unicodedata
import warnings
from collections import defaultdict
from dataclasses import dataclass

with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import fitz

from app.core.config import settings

# Kept local so this module stays independent of document_parser, which will
# import it. Mirrors document_parser._pair_tables and its TABLE_LABELS.
TABLE_LABELS = {"table"}
CELL_BAND_TOLERANCE_PX = 5.0


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int
    line_no: int
    word_no: int


@dataclass(frozen=True)
class PageText:
    words: list[Word]
    width: float
    height: float
    text: str


@dataclass(frozen=True)
class TextLayerDecision:
    usable: bool
    reason: str


def _is_private_use(character: str) -> bool:
    code = ord(character)
    return (
        0xE000 <= code <= 0xF8FF
        or 0xF0000 <= code <= 0xFFFFD
        or 0x100000 <= code <= 0x10FFFD
    )


def extract_page_text(
    file_data: bytes, page_number: int, password: str | None = None
) -> PageText:
    """Read one 1-based page's words and text in PDF point coordinates.

    An unreadable file or an out-of-range page yields an empty PageText, which
    the quality gate rejects, so the caller falls back to OCR.
    """
    empty = PageText([], 0.0, 0.0, "")
    if not isinstance(page_number, int) or page_number < 1 or not file_data:
        return empty
    try:
        with fitz.open(stream=file_data, filetype="pdf") as document:
            if document.needs_pass and (
                password is None or not document.authenticate(password)
            ):
                return empty
            if page_number > document.page_count:
                return empty
            page = document[page_number - 1]
            rect = page.rect
            words = [Word(*raw) for raw in page.get_text("words")]
            return PageText(words, rect.width, rect.height, page.get_text("text"))
    except Exception:
        return empty


def evaluate_text_layer(page_text: PageText) -> TextLayerDecision:
    """Decide whether the embedded text layer is trustworthy enough to use.

    Every rejection names the threshold or check that failed.
    """
    words = page_text.words
    if not words:
        return TextLayerDecision(False, "no_words: page has no word boxes")

    boxes = [(word.x0, word.y0, word.x1, word.y1) for word in words]
    if len(set(boxes)) == 1:
        return TextLayerDecision(
            False, "identical_word_boxes: every word has the same box"
        )

    if len(words) < settings.TEXT_LAYER_MIN_WORDS:
        return TextLayerDecision(
            False,
            f"min_words: {len(words)} < {settings.TEXT_LAYER_MIN_WORDS}",
        )

    text = page_text.text
    if not text or not text.strip():
        return TextLayerDecision(False, "no_text: page has no extracted text")
    nonspace = [character for character in text if not character.isspace()]
    if not nonspace:
        return TextLayerDecision(False, "no_text: page has no non-space characters")

    total = len(nonspace)
    private = sum(1 for character in nonspace if _is_private_use(character))
    private_ratio = private / total
    if private_ratio > settings.TEXT_LAYER_MAX_PRIVATE_USE_RATIO:
        return TextLayerDecision(
            False,
            f"max_private_use_ratio: {private_ratio:.3f} > "
            f"{settings.TEXT_LAYER_MAX_PRIVATE_USE_RATIO}",
        )

    control = sum(
        1 for character in nonspace if unicodedata.category(character) == "Cc"
    )
    control_ratio = control / total
    if control_ratio > settings.TEXT_LAYER_MAX_CONTROL_RATIO:
        return TextLayerDecision(
            False,
            f"max_control_ratio: {control_ratio:.3f} > "
            f"{settings.TEXT_LAYER_MAX_CONTROL_RATIO}",
        )

    symbols = sum(
        1 for character in nonspace if unicodedata.category(character).startswith("S")
    )
    symbol_ratio = symbols / total
    if symbol_ratio > settings.TEXT_LAYER_MAX_SYMBOL_RATIO:
        return TextLayerDecision(
            False,
            f"max_symbol_ratio: {symbol_ratio:.3f} > "
            f"{settings.TEXT_LAYER_MAX_SYMBOL_RATIO}",
        )

    alpha = sum(1 for character in nonspace if character.isalpha())
    alpha_ratio = alpha / total
    if alpha_ratio < settings.TEXT_LAYER_MIN_ALPHA_RATIO:
        return TextLayerDecision(
            False,
            f"min_alpha_ratio: {alpha_ratio:.3f} < "
            f"{settings.TEXT_LAYER_MIN_ALPHA_RATIO}",
        )
    if alpha < settings.TEXT_LAYER_MIN_ALPHA_CHARS:
        return TextLayerDecision(
            False,
            f"min_alpha_chars: {alpha} < {settings.TEXT_LAYER_MIN_ALPHA_CHARS}",
        )

    in_page = sum(
        1
        for word in words
        if 0 <= (word.x0 + word.x1) / 2 <= page_text.width
        and 0 <= (word.y0 + word.y1) / 2 <= page_text.height
    )
    in_page_ratio = in_page / len(words)
    if in_page_ratio < settings.TEXT_LAYER_MIN_IN_PAGE_RATIO:
        return TextLayerDecision(
            False,
            f"min_in_page_ratio: {in_page_ratio:.3f} < "
            f"{settings.TEXT_LAYER_MIN_IN_PAGE_RATIO}",
        )

    median_height = statistics.median(word.y1 - word.y0 for word in words)
    if not (
        settings.TEXT_LAYER_WORD_HEIGHT_MIN_PT
        <= median_height
        <= settings.TEXT_LAYER_WORD_HEIGHT_MAX_PT
    ):
        return TextLayerDecision(
            False,
            f"median_word_height: {median_height:.3f} outside "
            f"[{settings.TEXT_LAYER_WORD_HEIGHT_MIN_PT}, "
            f"{settings.TEXT_LAYER_WORD_HEIGHT_MAX_PT}]",
        )

    return TextLayerDecision(True, "ok")


def scale_words(page_text: PageText, dpi: int = 150) -> list[Word]:
    """Convert PDF point coordinates to raster pixel coordinates."""
    factor = dpi / 72.0
    return [
        Word(
            word.x0 * factor,
            word.y0 * factor,
            word.x1 * factor,
            word.y1 * factor,
            word.text,
            word.block_no,
            word.line_no,
            word.word_no,
        )
        for word in page_text.words
    ]


def serialize_words(words: list[Word]) -> dict:
    """Pack words into a JSON-safe payload for queueing between stages."""
    return {
        "words": [
            [
                word.x0,
                word.y0,
                word.x1,
                word.y1,
                word.text,
                word.block_no,
                word.line_no,
                word.word_no,
            ]
            for word in words
        ]
    }


def deserialize_words(payload: dict) -> list[Word]:
    """Rebuild words from serialize_words output, skipping bad entries."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("words")
    if not isinstance(entries, (list, tuple)):
        return []
    words = []
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 8:
            continue
        try:
            words.append(
                Word(
                    float(entry[0]),
                    float(entry[1]),
                    float(entry[2]),
                    float(entry[3]),
                    str(entry[4]),
                    int(entry[5]),
                    int(entry[6]),
                    int(entry[7]),
                )
            )
        except (TypeError, ValueError):
            continue
    return words


def _as_box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(
        isinstance(component, bool) or not isinstance(component, (int, float))
        for component in value
    ):
        return None
    box = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _centre(word: Word) -> tuple[float, float]:
    return (word.x0 + word.x1) / 2.0, (word.y0 + word.y1) / 2.0


def _contains(box: tuple[float, float, float, float], cx: float, cy: float) -> bool:
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _area(box: tuple[float, float, float, float]) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _cluster_edges(values: list[float]) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > CELL_BAND_TOLERANCE_PX:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _nearest_edge(value: float, edges: list[float]) -> int:
    return min(range(len(edges)), key=lambda index: abs(value - edges[index]))


def _reading_order(words: list[Word]) -> str:
    return " ".join(
        word.text
        for word in sorted(words, key=lambda item: (item.y0, item.x0))
        if word.text
    )


def _build_table_html(
    table_result: object, words: list[Word]
) -> tuple[str, set[int]] | None:
    """Rebuild table HTML from cell boxes, or None if geometry is unusable.

    The returned set holds the indices of the words consumed by the table, so
    they cannot also be written into a region block.
    """
    if not isinstance(table_result, dict):
        return None
    raw_boxes = table_result.get("cell_box_list")
    if not isinstance(raw_boxes, (list, tuple)):
        return None
    boxes = [box for box in (_as_box(value) for value in raw_boxes) if box is not None]
    if not boxes:
        return None

    x_edges = _cluster_edges([box[0] for box in boxes] + [box[2] for box in boxes])
    y_edges = _cluster_edges([box[1] for box in boxes] + [box[3] for box in boxes])
    if len(x_edges) < 2 or len(y_edges) < 2:
        return None

    cells = []
    for box in boxes:
        col_idx = _nearest_edge(box[0], x_edges)
        col_end = _nearest_edge(box[2], x_edges)
        row_idx = _nearest_edge(box[1], y_edges)
        row_end = _nearest_edge(box[3], y_edges)
        if col_end <= col_idx or row_end <= row_idx:
            continue
        cells.append(
            {
                "bbox": box,
                "row_idx": row_idx,
                "col_idx": col_idx,
                "rowspan": row_end - row_idx,
                "colspan": col_end - col_idx,
                "words": [],
            }
        )
    if not cells:
        return None

    used: set[int] = set()
    for index, word in enumerate(words):
        cx, cy = _centre(word)
        best = None
        for position, cell in enumerate(cells):
            if not _contains(cell["bbox"], cx, cy):
                continue
            area = _area(cell["bbox"])
            if best is None or area < best[0]:
                best = (area, position)
        if best is not None:
            cells[best[1]]["words"].append(word)
            used.add(index)

    rows: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for cell in cells:
        markup = (
            f'<td rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">'
            f"{html.escape(_reading_order(cell['words']))}</td>"
        )
        rows[cell["row_idx"]].append((cell["col_idx"], markup))

    parts = ["<table>"]
    for row_idx in sorted(rows):
        parts.append("<tr>")
        parts.extend(markup for _, markup in sorted(rows[row_idx]))
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts), used


def _compute_updates(result: dict, words: list[Word]) -> list[tuple[dict, str]]:
    blocks = result.get("parsing_res_list")
    if not isinstance(blocks, list):
        return []
    table_results = result.get("table_res_list")
    if not isinstance(table_results, list):
        table_results = []

    updates: list[tuple[dict, str]] = []
    table_used: set[int] = set()
    table_iter = iter(table_results)
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_label") not in TABLE_LABELS:
            continue
        built = _build_table_html(next(table_iter, None), words)
        if built is None:
            continue
        markup, used = built
        updates.append((block, markup))
        table_used |= used

    candidates = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_label") in TABLE_LABELS:
            continue
        bbox = _as_box(block.get("block_bbox"))
        if bbox is not None:
            candidates.append((block, bbox))

    assigned: dict[int, list[Word]] = defaultdict(list)
    for index, word in enumerate(words):
        if index in table_used:
            continue
        cx, cy = _centre(word)
        best = None
        for position, (_, bbox) in enumerate(candidates):
            if not _contains(bbox, cx, cy):
                continue
            area = _area(bbox)
            if best is None or area < best[0]:
                best = (area, position)
        if best is not None:
            assigned[best[1]].append(word)

    for position, (block, _) in enumerate(candidates):
        updates.append((block, _reading_order(assigned.get(position, []))))
    return updates


def fill_pruned(pruned: dict, words: list[Word]) -> dict:
    """Return a deep copy of pruned with its block content filled from words.

    Words go to the smallest-area region whose bbox contains their centre, or
    to the smallest matching table cell. Malformed geometry leaves the affected
    block alone, and any failure returns the copied input unchanged.
    """
    try:
        result = copy.deepcopy(pruned)
    except Exception:
        return pruned
    if not isinstance(result, dict):
        return result
    try:
        usable_words = [word for word in (words or []) if isinstance(word, Word)]
        updates = _compute_updates(result, usable_words)
    except Exception:
        return result
    for block, content in updates:
        block["block_content"] = content
    return result
