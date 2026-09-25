import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import filetype

from app.core.config import settings
from app.pipeline.document_parser import DocumentParser

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DOCS_DIR = os.path.join(HERE, "..", "test_docs")
GROUND_TRUTH_DIR = os.path.join(HERE, "..", "ground_truth")
TABLE_LABELS = {"table"}
TABLE_TITLE_LABELS = {"table_title", "table_caption"}
IMAGE_LABELS = {"image", "figure", "chart", "seal"}


# --------------------------------------------------------------------------- #
# Table HTML -> cell grid
# --------------------------------------------------------------------------- #
class _GridParser(HTMLParser):
    """Replay a table's cells into grid positions, spans included."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self._occupied = set()
        self._row = -1
        self._col = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row += 1
            self._col = 0
            return
        if tag not in ("td", "th"):
            return

        attr = dict(attrs)
        row_span = _positive_int(attr.get("rowspan"))
        col_span = _positive_int(attr.get("colspan"))

        self._row = max(self._row, 0)
        while (self._row, self._col) in self._occupied:
            self._col += 1
        for row in range(self._row, self._row + row_span):
            for col in range(self._col, self._col + col_span):
                self._occupied.add((row, col))

        # [row_idx, col_idx, row_span, col_span, is_header]
        self.cells.append([self._row, self._col, row_span, col_span, tag == "th"])
        self._col += col_span


def _positive_int(value):
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return 1


def cell_grid(html):
    parser = _GridParser()
    parser.feed(html or "")
    parser.close()
    return parser.cells


def grid_shape(cells):
    rows = max((row + row_span for row, _, row_span, _, _ in cells), default=0)
    cols = max((col + col_span for _, col, _, col_span, _ in cells), default=0)
    return rows, cols


# --------------------------------------------------------------------------- #
# Pruned result -> model graph
# --------------------------------------------------------------------------- #
def reading_order(blocks):
    """The page's blocks in the order their regions are written in.

    block_order is the model's reading order but is usable only when every block
    on the page has one. block_id indexes the layout areas in document order
    and is the fallback. Array order is the last resort.
    """
    if not blocks:
        return []
    for key in ("block_order", "block_id"):
        if all(isinstance(block.get(key), int) for block in blocks):
            return sorted(blocks, key=lambda block: block[key])
    return list(blocks)


def page_graph(page_number, pruned_page):
    """One page's expected regions and the table part each table region holds."""
    blocks = reading_order(pruned_page.get("parsing_res_list") or [])

    regions = []
    parts = []
    previous_title = None

    for order, block in enumerate(blocks):
        label = block.get("block_label")
        content = block.get("block_content")
        is_table = label in TABLE_LABELS

        regions.append(
            {
                "reading_order": order,
                "region_type": label,
                "block_label": label,
                "is_table": is_table,
                # A table's content becomes cells, and an image's becomes
                # nothing; everything else with content gets a RegionText.
                "has_text": bool(content) and not is_table and label not in IMAGE_LABELS,
            }
        )

        if is_table:
            cells = cell_grid(content)
            rows, cols = grid_shape(cells)
            parts.append(
                {
                    "page_number": page_number,
                    "region_reading_order": order,
                    "position_on_page": len(parts),
                    "row_count": rows,
                    "col_count": cols,
                    "cells": cells,
                    "title": previous_title,
                }
            )

        previous_title = content if label in TABLE_TITLE_LABELS else None

    for part in parts:
        part["table_count_on_page"] = len(parts)
        part["page_total_reading_order"] = len(blocks) - 1

    return {"page_number": page_number, "regions": regions}, parts


def continues(part, previous):
    """Is part the continuation of previous across a page break?

    Checks if part and previous are in consecutive pages and have
    the same number of columns
    """
    return (
        part["page_number"] == previous["page_number"] + 1
        and previous["region_reading_order"] == previous["page_total_reading_order"]
        and part["region_reading_order"] == 0
        and part["col_count"] > 0
        and part["col_count"] == previous["col_count"]
    )


def group_parts(parts, merge):
    groups = []
    for part in parts:
        if merge and groups and continues(part, groups[-1][-1]):
            groups[-1].append(part)
        else:
            groups.append([part])
    return groups


def build_table(group):
    """One expected Table row, with the RegionTable parts hanging off it."""
    table = {
        "row_count": sum(part["row_count"] for part in group),
        "col_count": group[0]["col_count"],
        "is_stitched": len(group) > 1,
        "title": next((part["title"] for part in group if part["title"]), None),
        "parts": [],
    }

    row_offset = 0
    for order, part in enumerate(group):
        table["parts"].append(
            {
                "reading_order": order,
                "page_number": part["page_number"],
                "region_reading_order": part["region_reading_order"],
                "row_start": row_offset,
                "row_end": row_offset + max(part["row_count"] - 1, 0),
                "col_start": 0,
                "col_end": max(part["col_count"] - 1, 0),
                # [row_idx, col_idx, row_span, col_span, is_header] per cell,
                # local to this part: the global row is row_start + row_idx.
                "cells": part["cells"],
            }
        )
        row_offset += part["row_count"]

    return table


def totals(pages, tables):
    return {
        "documents": 1,
        "pages": len(pages),
        "regions": sum(len(page["regions"]) for page in pages),
        "region_texts": sum(
            1 for page in pages for region in page["regions"] if region["has_text"]
        ),
        "tables": len(tables),
        "region_tables": sum(len(table["parts"]) for table in tables),
        "table_cells": sum(
            len(part["cells"]) for table in tables for part in table["parts"]
        ),
    }


def model_graph(pruned):
    pages = []
    parts = []
    for index, pruned_page in enumerate(pruned):
        page, page_parts = page_graph(index + 1, pruned_page)
        pages.append(page)
        parts.extend(page_parts)

    graph = {"pages": pages}
    for key, merge in (("unmerged", False), ("merged", True)):
        tables = [build_table(group) for group in group_parts(parts, merge)]
        graph[key] = {"counts": totals(pages, tables), "tables": tables}
    return graph


# --------------------------------------------------------------------------- #
# Driving the cluster
# --------------------------------------------------------------------------- #


def pruned_for(parser):
    """Obtain pruned result from pages one at a time"""
    responses = []
    for index, page in enumerate(parser.pages, start=1):
        print(f"    page {index}/{len(parser.pages)}", flush=True)
        responses.append(parser.process_page(page))
    parser.parsed_result = responses
    return parser.get_pruned_result()


def record(name):
    """Record ground truth for a given document."""
    
    path = os.path.join(TEST_DOCS_DIR, name)
    with open(path, "rb") as handle:
        data = handle.read()

    kind = filetype.guess(data)
    truth = {
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "detected_extension": kind.extension if kind else None,
    }

    parser = DocumentParser()
    try:
        parser.load_file(data)
    except ValueError as error:
        # No loader claims this file: the graph half does not apply.
        truth.update(
            kind="unsupported",
            expected_pages=0,
            expected_error_message=str(error),
        )
        return truth

    truth.update(kind="processable", expected_pages=len(parser.pages))
    truth["model_graph"] = model_graph(pruned_for(parser))
    return truth



def dumps(truth):
    """Dump the ground truth as a JSON string with compact cell lines."""
    
    # Identifies cell arrays
    cell_lines = re.compile(
        r"\[\s+(-?\d+),\s+(-?\d+),\s+(-?\d+),\s+(-?\d+),\s+(true|false)\s+\]"
    )
    
    # Dump the JSON with compact cell lines
    return cell_lines.sub(r"[\1, \2, \3, \4, \5]", json.dumps(truth, indent=2)) + "\n"


def main(names):
    if settings.INFERENCE_SERVICE_URL.startswith("http://gateway"):
        raise SystemExit(
            "INFERENCE_SERVICE_URL points at the mock gateway, which returns the "
            "same canned blocks for every page. Point it at the cluster."
        )

    os.makedirs(GROUND_TRUTH_DIR, exist_ok=True)
    names = names or sorted(
        name
        for name in os.listdir(TEST_DOCS_DIR)
        if os.path.isfile(os.path.join(TEST_DOCS_DIR, name)) and not name.startswith(".")
    )

    for name in names:
        print(f"  {name}", flush=True)
        truth = record(name)
        out = os.path.join(GROUND_TRUTH_DIR, f"{name}.json")
        with open(out, "w") as handle:
            handle.write(dumps(truth))
        print(f"    -> {os.path.relpath(out, HERE)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
