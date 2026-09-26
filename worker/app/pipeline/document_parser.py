import asyncio
import logging
import uuid
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any, ClassVar

import filetype
import requests
from yarrow_db.models import (
    Base,
    Document,
    Page,
    Region,
    RegionTable,
    RegionText,
    Table,
    TableCell,
)

from app.core.config import settings
from app.pipeline.file_loaders.file_loader import FileLoader
from app.pipeline.file_loaders.gif_loader import GIFLoader
from app.pipeline.file_loaders.jpeg_loader import JPEGLoader
from app.pipeline.file_loaders.jpg_loader import JPGLoader
from app.pipeline.file_loaders.pdf_loader import PDFLoader
from app.pipeline.file_loaders.png_loader import PNGLoader

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
Box = tuple[float, float, float, float]
BlockWithTable = tuple[JsonDict, JsonDict | None]
CellBBoxPair = tuple[JsonDict, Box | None]

LAYOUT_SCORE_IOU_THRESHOLD = 0.5

TABLE_LABELS = {"table"}
TABLE_TITLE_LABELS = {"table_title", "table_caption"}
IMAGE_LABELS = {"image", "figure", "chart", "seal"}


def iou(a: Box, b: Box) -> float:
    """Compute the Intersection over Union (IoU) of two bounding boxes.

    Each bounding box is represented as a sequence of four floats:
    (x0, y0, x1, y1).

    Returns a float in [0, 1] representing the IoU.
    """
    inter_w = min(a[2], b[2]) - max(a[0], b[0])
    inter_h = min(a[3], b[3]) - max(a[1], b[1])
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    inter = inter_w * inter_h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _uniform_edges(lo: float, hi: float, count: int) -> list[float]:
    """Generates the boundary coordinates for an evenly divided interval."""
    if count <= 0:
        return [lo, hi]

    step = (hi - lo) / count
    return [lo + index * step for index in range(count + 1)]


def _positive_int(value: str | None) -> int:
    """Cap the value at a minimum of 1"""
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return 1


class _TableHtmlParser(HTMLParser):
    """Walks table HTML into TableCellSpecs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[JsonDict] = []
        self._occupied: set[tuple[int, int]] = set()
        self._row = -1
        self._col = 0
        self._current_cell_data: JsonDict | None = None
        self._depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "tr":
            self._row += 1
            self._col = 0
            return

        if tag not in ("td", "th"):
            if self._current_cell_data is not None:
                # There is a nested html object in the current cell, increment depth
                self._depth += 1
            return

        # Extract tag attributes for the cell
        attr: dict[str, str | None] = dict(attrs)
        row_span = _positive_int(attr.get("rowspan"))
        col_span = _positive_int(attr.get("colspan"))

        self._row = max(self._row, 0)

        # Find the next unoccupied position in the current row.
        while (self._row, self._col) in self._occupied:
            self._col += 1

        # Mark all positions covered by this cell as occupied.
        for row in range(self._row, self._row + row_span):
            for col in range(self._col, self._col + col_span):
                self._occupied.add((row, col))

        self._current_cell_data = {
            "row_idx": self._row,
            "col_idx": self._col,
            "row_span": row_span,
            "col_span": col_span,
            "text": "",
            "is_header": tag == "th",
        }

        self._col += col_span

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._close_cell()
        elif self._current_cell_data is not None and self._depth > 0:
            # Nested object closed, decrementing depth
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current_cell_data is not None:
            self._current_cell_data["text"] += data

    def close(self) -> None:
        # tolerate an unclosed trailing cell
        super().close()
        self._close_cell()

    def _close_cell(self) -> None:
        if self._current_cell_data is None:
            return
        self._current_cell_data["text"] = " ".join(
            self._current_cell_data["text"].split()
        )
        self.cells.append(self._current_cell_data)
        self._current_cell_data = None
        self._depth = 0


class DocumentParser:
    FILE_EXTENSION_TO_LOADER: ClassVar[dict[str, FileLoader]] = {
        "png": PNGLoader(),
        "jpg": JPGLoader(),
        "jpeg": JPEGLoader(),
        "gif": GIFLoader(),
        "pdf": PDFLoader(),
    }

    def __init__(self) -> None:
        self.pages: list[str] = []
        self.parsed_result: list[JsonDict] = []
        self.page_errors: dict[int, str] = {}

    @property
    def failed_page_numbers(self) -> list[int]:
        """1-based page numbers that produced no result."""
        return sorted(index + 1 for index in self.page_errors)

    def detect_extension(self, file_data: bytes) -> str | None:
        kind = filetype.guess(file_data)
        return None if kind is None else kind.extension

    def load_file(self, file_data: bytes) -> None:
        file_extension = self.detect_extension(file_data)

        loader = (
            self.FILE_EXTENSION_TO_LOADER.get(file_extension)
            if file_extension is not None
            else None
        )
        if loader is None:
            raise ValueError(f"No loader found for file extension: {file_extension}")

        self.pages = loader.process_file(file_data)

    def process_page(self, page_data: str) -> JsonDict:
        headers = {"Content-Type": "application/json"}
        if settings.INFERENCE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.INFERENCE_API_KEY}"

        response = requests.post(
            settings.INFERENCE_SERVICE_URL,
            json={"file": page_data, "fileType": 1},
            headers=headers,
            timeout=settings.INFERENCE_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        return response.json()

    def _process_page_isolated(self, page_index: int, page_data: str) -> JsonDict:
        error_message = ""

        try:
            return self.process_page(page_data)
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"
            logger.error(f"Page {page_index + 1} processing failed: ({error_message})")

        self.page_errors[page_index] = error_message
        return {}

    async def process_async(self) -> None:
        self.page_errors = {}
        tasks = [
            asyncio.to_thread(self._process_page_isolated, index, page)
            for index, page in enumerate(self.pages)
        ]

        self.parsed_result = list(await asyncio.gather(*tasks))

    def process_sync(self) -> None:
        asyncio.run(self.process_async())

    def get_pruned_result(self) -> list[JsonDict]:
        return [
            response.get("result")
            .get("layoutParsingResults", {})[0]
            .get("prunedResult")
            for response in self.parsed_result
        ]

    def get_markdown(self) -> list[str]:
        markdown: list[str] = []
        for response in self.parsed_result:
            markdown.extend(
                (item.get("markdown") or {}).get("text", "")
                for item in self._result_items(response)
            )
        return markdown

    def _consecutive_parts(self, part_prev, part_curr):
        """Is part the continuation of previous across a page break?

        Checks if part and previous are in consecutive pages and have
        the same number of columns
        """
        return (
            part_curr["page_index"] == part_prev["page_index"] + 1
            and part_prev["position"] == part_prev["page_table_count"] - 1
            and part_curr["position"] == 0
            and part_curr["col_count"] > 0
            and part_curr["col_count"] == part_prev["col_count"]
        )

    def _group_region_tables(self, region_table_data, merge_consecutive_tables):
        groups: list[list[JsonDict]] = []

        for part in region_table_data:
            if (
                merge_consecutive_tables
                and groups
                and self._consecutive_parts(groups[-1][-1], part)
            ):
                groups[-1].append(part)
            else:
                groups.append([part])

        return groups

    def _get_cell_bbox_pair(
        self,
        cells: list[JsonDict],
        cell_boxes: list[Box | None],
        table_box: Box | None,
        row_count: int,
        col_count: int,
    ) -> Iterator[CellBBoxPair]:
        """Pair every cell with its bounding box."""

        # Prefer explicit cell boxes when there is one for every cell.
        if len(cell_boxes) == len(cells) and all(box is not None for box in cell_boxes):
            yield from zip(cells, cell_boxes)
            return

        # Cannot derive geometry without a table bbox.
        if table_box is None:
            for cell in cells:
                yield cell, None
            return

        x_edges = _uniform_edges(
            table_box[0],
            table_box[2],
            col_count,
        )
        y_edges = _uniform_edges(
            table_box[1],
            table_box[3],
            row_count,
        )

        for cell in cells:
            box: Box = (
                x_edges[cell["col_idx"]],
                y_edges[cell["row_idx"]],
                x_edges[cell["col_idx"] + cell["col_span"]],
                y_edges[cell["row_idx"] + cell["row_span"]],
            )

            yield cell, box

    def _build_table(
        self,
        document: Document,
        group: list[JsonDict],
    ) -> tuple[list[Table], list[RegionTable], list[TableCell]]:
        tables: list[Table] = []
        region_tables: list[RegionTable] = []
        table_cells: list[TableCell] = []
        title = None

        for part in group:
            if part["title"]:
                title = part["title"]

        table = Table(
            id=uuid.uuid4(),
            document_id=document.id,
            row_count=sum(part["row_count"] for part in group),
            col_count=group[0]["col_count"],
            is_stitched=len(group) > 1,
            title=title,
        )
        tables.append(table)

        row_offset = 0

        # Constructs the region tables of the corresponding table
        for reading_order, part in enumerate(group):
            region_table = RegionTable(
                id=uuid.uuid4(),
                region_id=part["region"].id,
                table_id=table.id,
                reading_order=reading_order,
                row_start=row_offset,
                row_end=row_offset + max(part["row_count"] - 1, 0),
                col_start=0,
                col_end=max(part["col_count"] - 1, 0),
            )
            region_tables.append(region_table)

            # Constructs the table cells of the corresponding region table
            for cell, bbox in self._get_cell_bbox_pair(
                cells=part["cells"],
                cell_boxes=part["cell_boxes"],
                table_box=part.get("bbox"),
                row_count=part["row_count"],
                col_count=part["col_count"],
            ):
                print(cell)
                table_cell = TableCell(
                    id=uuid.uuid4(),
                    region_table_id=region_table.id,
                    row_idx=cell["row_idx"],
                    col_idx=cell["col_idx"],
                    row_span=cell["row_span"],
                    col_span=cell["col_span"],
                    text_content=cell["text"],
                    x0=bbox[0] if bbox else 0,
                    y0=bbox[1] if bbox else 0,
                    x1=bbox[2] if bbox else 0,
                    y1=bbox[3] if bbox else 0,
                    is_header=cell["is_header"],
                )
                table_cells.append(table_cell)

            row_offset += part["row_count"]

        return tables, region_tables, table_cells

    def _pair_tables(self, page_data: JsonDict) -> list[BlockWithTable]:
        """Attach each region data entry to the table_res_list item it describes

        Returns a list of tuple that represents the matching. If the region data
        is not a table, then it pairs with None.
        """
        blocks: list[JsonDict] = page_data.get("parsing_res_list") or []
        table_results = iter(page_data.get("table_res_list", []))

        return [
            (
                block,
                next(table_results, None)
                if block.get("block_label") in TABLE_LABELS
                else None,
            )
            for block in blocks
        ]

    def _get_bbox_score(
        self,
        layout_det_res: JsonDict,
        label: str | None,
        bbox: list[float] | None,
    ) -> float | None:
        if not bbox:
            return None

        candidates = []

        # region label, bbox, score pairs
        for box_det_res in layout_det_res.get("boxes") or []:
            coordinate_det = box_det_res.get("coordinate")
            label_det, score_det = box_det_res.get("label"), box_det_res.get("score")
            if (
                not coordinate_det
                or len(coordinate_det) != 4
                or label is None
                or score_det is None
            ):
                continue

            box = (
                float(coordinate_det[0]),
                float(coordinate_det[1]),
                float(coordinate_det[2]),
                float(coordinate_det[3]),
            )

            if label_det == label:
                candidates.append((iou(bbox, box), score_det))

        if not candidates:
            return None

        best_iou, score = max(candidates, key=lambda pair: pair[0])
        return score if best_iou >= LAYOUT_SCORE_IOU_THRESHOLD else None

    def _sort_region_data(self, pairs: list[BlockWithTable]) -> list[BlockWithTable]:
        """The page's region data in the order their regions are written in.

        block_order is the model's reading order but is usable only when every block
        on the page has one. block_id indexes the layout areas in document order
        and is the fallback. Array order is the last resort.
        """
        if not pairs:
            return []
        for key in ("block_order", "block_id"):
            if all(isinstance(block.get(key), int) for block, _ in pairs):
                return sorted(pairs, key=lambda pair: pair[0][key])
        return list(pairs)

    def _construct_regions(self, page_index, page_id, page_data):
        regions = []  # Any region except table regions
        region_tables_data = []  # Partially initialized regions tables

        previous_title: str | None = None

        region_data_to_table_results: list[tuple[JsonDict, JsonDict | None]] = (
            self._pair_tables(page_data)
        )
        region_data_to_table_results = self._sort_region_data(
            region_data_to_table_results
        )
        page_table_count = sum(
            int(region_data.get("block_label") in TABLE_LABELS)
            for region_data, _ in region_data_to_table_results
        )

        for reading_order, (region_data, table_result) in enumerate(
            region_data_to_table_results
        ):
            label: str | None = region_data.get("block_label")
            content: str | None = region_data.get("block_content")
            bbox: list[float] | None = region_data.get("block_bbox")

            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(component is None for component in bbox):
                bbox = None
            else:
                bbox = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))

            region = Region(
                id=uuid.uuid4(),
                page_id=page_id,
                reading_order=reading_order,
                region_type=label,
                x0=bbox[0] if bbox else 0,
                y0=bbox[1] if bbox else 0,
                x1=bbox[2] if bbox else 0,
                y1=bbox[3] if bbox else 0,
                confidence=self._get_bbox_score(
                    page_data.get("layout_det_res", {}), label, bbox
                ),
            )
            regions.append(region)

            if label in TABLE_LABELS:
                parser = _TableHtmlParser()
                parser.feed(content)
                parser.close()

                cell_boxes: list[Box | None] = [
                    (
                        float(box[0]),
                        float(box[1]),
                        float(box[2]),
                        float(box[3]),
                    )
                    for box in (table_result or {}).get("cell_box_list", [])
                    if isinstance(box, (list, tuple)) and len(box) == 4
                ]

                region_tables_data.append(
                    {
                        "page_index": page_index,
                        "page_id": page_id,
                        "position": len(region_tables_data),
                        "page_table_count": page_table_count,
                        "region": region,
                        "result": table_result,
                        "bbox": bbox,
                        "cells": parser.cells,
                        "cell_boxes": cell_boxes,
                        "row_count": max(
                            (
                                cell["row_idx"] + cell["row_span"]
                                for cell in parser.cells
                            ),
                            default=0,
                        ),
                        "col_count": max(
                            (
                                cell["col_idx"] + cell["col_span"]
                                for cell in parser.cells
                            ),
                            default=0,
                        ),
                        "content": content,
                        "title": previous_title,
                    }
                )

            elif label not in IMAGE_LABELS and content:
                regions.append(
                    RegionText(
                        id=uuid.uuid4(),
                        region_id=region.id,
                        text_content=content,
                        confidence=self._get_bbox_score(
                            page_data.get("layout_det_res", {}), label, bbox
                        ),
                    )
                )

            previous_title = content if label in TABLE_TITLE_LABELS else None

        return regions, region_tables_data

    def to_model_objects(
        self, document: Document, merge_consecutive_tables: bool = False
    ) -> list[Base]:
        """
        Converts the parsed result into model objects.

        Args:
            document: The Document these pages belong to
            merge_consecutive_tables (bool): Whether to merge consecutive tables across pages that
            have the same number of columns

        Returns: A flat list of all model objects created
        """
        pruned_result = self.get_pruned_result()
        document.page_count = len(pruned_result)

        pages = []
        regions = []
        tables = []
        all_objects = []

        # Information about region table necessary for future table construction
        region_table_data = []

        # Reconstruct each page sequentially, constructing regions
        for page_index, page_data in enumerate(pruned_result):
            if page_data == {}:
                # Page processing failed, continue
                continue

            # Construct page object
            page_id = uuid.uuid4()
            error_message = self.page_errors.get(page_index)
            page = Page(
                id=page_id,
                document_id=document.id,
                page_number=page_index + 1,
                width=page_data.get("width"),
                height=page_data.get("height"),
                status="failed" if error_message else "completed",
                error_message=error_message,
            )
            pages.append(page)

            # Construct regions for each page
            page_regions, region_tables_data_per_page = self._construct_regions(
                page_index, page_id, page_data
            )

            regions.extend(page_regions)
            region_table_data.extend(region_tables_data_per_page)

        # Iterate through each table region and merge consecutive tables if needed
        groups = self._group_region_tables(region_table_data, merge_consecutive_tables)

        # Construct tables and their respective objects
        for group in groups:
            tables_per_group, region_tables_per_group, table_cells_per_group = (
                self._build_table(document, group)
            )
            tables.extend(tables_per_group)
            regions.extend(region_tables_per_group)
            regions.extend(table_cells_per_group)

        # Return a flat list of all objects
        all_objects.extend(pages)
        all_objects.extend(regions)
        all_objects.extend(tables)

        return all_objects
