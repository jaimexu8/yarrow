from __future__ import annotations

from typing import Any

JsonDict = dict[str, Any]

# A block wider than this fraction of the page is treated as spanning: it is
# excluded from the gutter search and, on a multi-column page, a horizontal
# separator at its vertical position (titles, wide tables, footers). A real
# column never exceeds half the page, so it must not count as coverage.
FULL_WIDTH_FRACTION = 0.5

# A block narrower than this fraction of the page does not count as coverage:
# a small centered element (a short heading, a page number) sitting astride
# the gutter would otherwise bridge the two columns into one band. Such
# blocks are still sorted normally; they just cannot veto a gutter.
NARROW_FRACTION = 0.10

# An uncovered horizontal run wider than this fraction of the page is a
# column gutter. Narrower runs are inter-block margins (on a 1275 px wide
# page this keeps 20 px side margins out of the search).
GUTTER_FRACTION = 0.02

_Bbox = tuple[float, float, float, float]


def order_blocks(blocks: list[JsonDict], page_width: float | None) -> list[JsonDict]:
    """The page's blocks in reading order.

    Multi-column pages are read column by column: full-width blocks (titles,
    wide tables, footers) sit at their vertical position and split the page
    into horizontal bands, and within a band each column is read top to
    bottom before moving to the next column. Single-column pages keep the
    model's order.
    """
    if not blocks:
        return []
    if (
        not isinstance(page_width, (int, float))
        or isinstance(page_width, bool)
        or page_width <= 0
    ):
        return _legacy_order(blocks)
    bboxes = [_bbox(block) for block in blocks]
    if any(bbox is None for bbox in bboxes):
        return _legacy_order(blocks)

    full_width = [
        x1 - x0 > FULL_WIDTH_FRACTION * page_width for (x0, _, x1, _) in bboxes
    ]
    if all(full_width):
        return _legacy_order(blocks)

    bands = _column_bands(bboxes, full_width, page_width)
    if len(bands) <= 1:
        # One column: there is no column to reorder, but geometry still
        # ranks blocks at the same height, with the model's complete
        # enumeration breaking the ties.
        if _has_full_order(blocks):
            ordered = sorted(
                range(len(blocks)),
                key=lambda i: (bboxes[i][1], _model_order(blocks[i]), i),
            )
            return [blocks[i] for i in ordered]
        return _legacy_order(blocks)

    spanning = sorted(
        (
            i
            for i in range(len(blocks))
            if len(_overlapping_bands(bboxes[i], bands)) >= 2
        ),
        key=lambda i: (bboxes[i][1], _model_order(blocks[i]), i),
    )
    within = [i for i in range(len(blocks)) if i not in set(spanning)]

    output: list[int] = []
    for position, separator in enumerate(spanning):
        band = [i for i in within if _band_below_spans(bboxes, spanning, i) == position]
        output.extend(_column_order(band, bands, bboxes, blocks))
        output.append(separator)
    band = [
        i for i in within if _band_below_spans(bboxes, spanning, i) == len(spanning)
    ]
    output.extend(_column_order(band, bands, bboxes, blocks))
    return [blocks[i] for i in output]


def _legacy_order(blocks: list[JsonDict]) -> list[JsonDict]:
    """The previous sort: model block_order, then block_id, then array order."""
    for key in ("block_order", "block_id"):
        if all(isinstance(block.get(key), int) for block in blocks):
            return sorted(blocks, key=lambda block: block[key])
    return list(blocks)


def _has_full_order(blocks: list[JsonDict]) -> bool:
    return all(isinstance(block.get("block_order"), int) for block in blocks)


def _model_order(block: JsonDict) -> float:
    order = block.get("block_order")
    return order if isinstance(order, int) else float("inf")


def _bbox(block: JsonDict) -> _Bbox | None:
    bbox = block.get("block_bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox
    ):
        return None
    x0, y0, x1, y1 = (float(value) for value in bbox)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _column_bands(
    bboxes: list[_Bbox], full_width: list[bool], page_width: float
) -> list[tuple[float, float]]:
    """The x-extent of each column, separated by the gutters between them."""
    intervals = sorted(
        (x0, x1)
        for (x0, _, x1, _), is_full in zip(bboxes, full_width)
        if not is_full and x1 - x0 >= NARROW_FRACTION * page_width
    )
    merged = _merge_intervals(intervals)

    gutters: list[tuple[float, float]] = []
    x = 0.0
    for a, b in merged:
        # Only interior gaps separate columns; a gap touching the page edge
        # is a margin.
        if x > 0 and a < page_width and a - x > GUTTER_FRACTION * page_width:
            gutters.append((x, a))
        x = max(x, b)

    bands: list[tuple[float, float]] = []
    start = 0.0
    for gutter_left, gutter_right in gutters:
        bands.append((start, gutter_left))
        start = gutter_right
    bands.append((start, page_width))
    return bands


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _overlapping_bands(bbox: _Bbox, bands: list[tuple[float, float]]) -> list[int]:
    x0, _, x1, _ = bbox
    return [
        position
        for position, (left, right) in enumerate(bands)
        if min(x1, right) - max(x0, left) > 0
    ]


def _band_below_spans(bboxes: list[_Bbox], spanning: list[int], i: int) -> int:
    """The horizontal band i sits in: the number of spanning blocks whose
    top is at or above i's top."""
    return sum(1 for s in spanning if bboxes[s][1] <= bboxes[i][1])


def _column_order(
    indices: list[int],
    bands: list[tuple[float, float]],
    bboxes: list[_Bbox],
    blocks: list[JsonDict],
) -> list[int]:
    """A band's blocks: each column left to right, top to bottom within it."""
    output: list[int] = []
    for band in range(len(bands)):
        in_band = [i for i in indices if _dominant_band(bboxes[i], bands) == band]
        in_band.sort(key=lambda i: (bboxes[i][1], _model_order(blocks[i]), i))
        output.extend(in_band)
    return output


def _dominant_band(bbox: _Bbox, bands: list[tuple[float, float]]) -> int:
    """The column a non-spanning block belongs to: the band it overlaps most."""
    x0, _, x1, _ = bbox
    best, best_overlap = 0, -1.0
    for position, (left, right) in enumerate(bands):
        overlap = min(x1, right) - max(x0, left)
        if overlap > best_overlap:
            best, best_overlap = position, overlap
    return best
