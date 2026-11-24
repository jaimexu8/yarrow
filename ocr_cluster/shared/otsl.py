"""Convert OTSL v1.0 table markup to HTML for the gateway's direct path.

PaddleX converts the six OTSL v1.0 tags (``<fcel>``, ``<ecel>``, ``<nl>``,
``<lcel>``, ``<ucel>``, ``<xcel>``) into an HTML table before returning a crop.
SGLang's ``/v1/chat/completions`` does not, so the gateway has to do it itself
when it calls the VLM directly. This is a self-contained port of
``paddlex.inference.pipelines.paddleocr_vl.uilts.convert_otsl_to_html`` and the
helpers it needs. It uses only the stdlib and pydantic; no paddlex or numpy.
"""

from __future__ import annotations

import html
import itertools
import re

from pydantic import BaseModel, computed_field

# OTSL v1.0 tag set.
OTSL_NL = "<nl>"
OTSL_FCEL = "<fcel>"
OTSL_ECEL = "<ecel>"
OTSL_LCEL = "<lcel>"
OTSL_UCEL = "<ucel>"
OTSL_XCEL = "<xcel>"
_OTSL_TAGS = (
    OTSL_NL,
    OTSL_FCEL,
    OTSL_ECEL,
    OTSL_LCEL,
    OTSL_UCEL,
    OTSL_XCEL,
)

_NON_CAPTURING_TAG_GROUP = "(?:<fcel>|<ecel>|<nl>|<lcel>|<ucel>|<xcel>)"
OTSL_FIND_PATTERN = re.compile(
    f"{_NON_CAPTURING_TAG_GROUP}.*?(?={_NON_CAPTURING_TAG_GROUP}|$)", flags=re.DOTALL
)


class TableCell(BaseModel):
    """One OTSL cell, with its row/column span and grid offsets."""

    row_span: int = 1
    col_span: int = 1
    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    text: str
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False


class TableData(BaseModel):
    """A table's cells and dimensions, with a computed 2-D grid."""

    table_cells: list[TableCell] = []
    num_rows: int = 0
    num_cols: int = 0

    @computed_field
    @property
    def grid(self) -> list[list[TableCell]]:
        """Fill a rows x cols grid, letting spanned cells overwrite placeholders."""
        table_data = [
            [
                TableCell(
                    text="",
                    start_row_offset_idx=i,
                    end_row_offset_idx=i + 1,
                    start_col_offset_idx=j,
                    end_col_offset_idx=j + 1,
                )
                for j in range(self.num_cols)
            ]
            for i in range(self.num_rows)
        ]
        for cell in self.table_cells:
            for i in range(
                min(cell.start_row_offset_idx, self.num_rows),
                min(cell.end_row_offset_idx, self.num_rows),
            ):
                for j in range(
                    min(cell.start_col_offset_idx, self.num_cols),
                    min(cell.end_col_offset_idx, self.num_cols),
                ):
                    table_data[i][j] = cell
        return table_data


def otsl_extract_tokens_and_text(s: str) -> tuple[list[str], list[str]]:
    """Split an OTSL string into its tags and the interleaved text chunks."""
    pattern = "(" + "|".join(_OTSL_TAGS) + ")"
    tokens = re.findall(pattern, s)
    text_parts = re.split(pattern, s)
    return tokens, [part for part in text_parts if part.strip()]


def otsl_parse_texts(
    texts: list[str], tokens: list[str]
) -> tuple[list[TableCell], list[list[str]]]:
    """Turn interleaved OTSL tags/text into TableCells plus a row token matrix."""
    split_row_tokens = [
        list(group)
        for is_newline, group in itertools.groupby(tokens, lambda token: token == OTSL_NL)
        if not is_newline
    ]
    table_cells: list[TableCell] = []
    r_idx = 0
    c_idx = 0

    # Pad every row to the same width so spans can be measured uniformly.
    if split_row_tokens:
        max_cols = max(len(row) for row in split_row_tokens)
        for row in split_row_tokens:
            while len(row) < max_cols:
                row.append(OTSL_ECEL)
        new_texts: list[str] = []
        text_idx = 0
        for row in split_row_tokens:
            for token in row:
                new_texts.append(token)
                if text_idx < len(texts) and texts[text_idx] == token:
                    text_idx += 1
                    if text_idx < len(texts) and texts[text_idx] not in _OTSL_TAGS:
                        new_texts.append(texts[text_idx])
                        text_idx += 1
            new_texts.append(OTSL_NL)
            if text_idx < len(texts) and texts[text_idx] == OTSL_NL:
                text_idx += 1
        texts = new_texts

    def count_right(row_tokens, col, row, which) -> int:
        span = 0
        cursor = col
        while row_tokens[row][cursor] in which:
            cursor += 1
            span += 1
            if cursor >= len(row_tokens[row]):
                return span
        return span

    def count_down(row_tokens, col, row, which) -> int:
        span = 0
        cursor = row
        while row_tokens[cursor][col] in which:
            cursor += 1
            span += 1
            if cursor >= len(row_tokens):
                return span
        return span

    for i, text in enumerate(texts):
        cell_text = ""
        if text in (OTSL_FCEL, OTSL_ECEL):
            row_span = 1
            col_span = 1
            right_offset = 1
            if text != OTSL_ECEL:
                cell_text = texts[i + 1]
                right_offset = 2

            next_right_cell = (
                texts[i + right_offset] if i + right_offset < len(texts) else ""
            )
            next_bottom_cell = ""
            if (
                r_idx + 1 < len(split_row_tokens)
                and c_idx < len(split_row_tokens[r_idx + 1])
            ):
                next_bottom_cell = split_row_tokens[r_idx + 1][c_idx]

            if next_right_cell in (OTSL_LCEL, OTSL_XCEL):
                col_span += count_right(
                    split_row_tokens, c_idx + 1, r_idx, (OTSL_LCEL, OTSL_XCEL)
                )
            if next_bottom_cell in (OTSL_UCEL, OTSL_XCEL):
                row_span += count_down(
                    split_row_tokens, c_idx, r_idx + 1, (OTSL_UCEL, OTSL_XCEL)
                )

            table_cells.append(
                TableCell(
                    text=cell_text.strip(),
                    row_span=row_span,
                    col_span=col_span,
                    start_row_offset_idx=r_idx,
                    end_row_offset_idx=r_idx + row_span,
                    start_col_offset_idx=c_idx,
                    end_col_offset_idx=c_idx + col_span,
                )
            )
        if text in (OTSL_FCEL, OTSL_ECEL, OTSL_LCEL, OTSL_UCEL, OTSL_XCEL):
            c_idx += 1
        if text == OTSL_NL:
            r_idx += 1
            c_idx = 0
    return table_cells, split_row_tokens


def export_to_html(table_data: TableData) -> str:
    """Render a TableData as an HTML table, keeping row/col spans."""
    if not table_data.table_cells:
        return ""
    body = ""
    grid = table_data.grid
    for i in range(table_data.num_rows):
        body += "<tr>"
        for j in range(table_data.num_cols):
            cell: TableCell = grid[i][j]
            rowspan, rowstart = cell.row_span, cell.start_row_offset_idx
            colspan, colstart = cell.col_span, cell.start_col_offset_idx
            if rowstart != i or colstart != j:
                continue
            content = html.escape(cell.text.strip())
            celltag = "th" if cell.column_header else "td"
            opening_tag = celltag
            if rowspan > 1:
                opening_tag += f' rowspan="{rowspan}"'
            if colspan > 1:
                opening_tag += f' colspan="{colspan}"'
            body += f"<{opening_tag}>{content}</{celltag}>"
        body += "</tr>"
    return f"<table>{body}</table>"


def otsl_pad_to_sqr_v2(otsl_str: str) -> str:
    """Pad OTSL rows to a common width with ``<ecel>`` so spans can be counted."""
    assert isinstance(otsl_str, str)
    otsl_str = otsl_str.strip()
    if OTSL_NL not in otsl_str:
        return otsl_str + OTSL_NL
    lines = otsl_str.split(OTSL_NL)
    row_data = []
    for line in lines:
        if not line:
            continue
        raw_cells = OTSL_FIND_PATTERN.findall(line)
        if not raw_cells:
            continue
        total_len = len(raw_cells)
        min_len = 0
        for i, cell_str in enumerate(raw_cells):
            if cell_str.startswith(OTSL_FCEL):
                min_len = i + 1
        row_data.append(
            {"raw_cells": raw_cells, "total_len": total_len, "min_len": min_len}
        )
    if not row_data:
        return OTSL_NL

    global_min_width = max(row["min_len"] for row in row_data)
    max_total_len = max(row["total_len"] for row in row_data)
    search_start = global_min_width
    search_end = max(global_min_width, max_total_len)
    min_total_cost = float("inf")
    optimal_width = search_end

    for width in range(search_start, search_end + 1):
        current_total_cost = sum(abs(row["total_len"] - width) for row in row_data)
        if current_total_cost < min_total_cost:
            min_total_cost = current_total_cost
            optimal_width = width

    repaired_lines = []
    for row in row_data:
        cells = row["raw_cells"]
        current_len = len(cells)
        if current_len > optimal_width:
            new_cells = cells[:optimal_width]
        else:
            new_cells = cells + [OTSL_ECEL] * (optimal_width - current_len)
        repaired_lines.append("".join(new_cells))
    return OTSL_NL.join(repaired_lines) + OTSL_NL


def convert_otsl_to_html(otsl_content: str) -> str:
    """Convert an OTSL v1.0 string to an HTML table (empty string if no cells)."""
    otsl_content = otsl_pad_to_sqr_v2(otsl_content)
    tokens, mixed_texts = otsl_extract_tokens_and_text(otsl_content)
    table_cells, split_row_tokens = otsl_parse_texts(mixed_texts, tokens)
    table_data = TableData(
        num_rows=len(split_row_tokens),
        num_cols=(max(len(row) for row in split_row_tokens) if split_row_tokens else 0),
        table_cells=table_cells,
    )
    return export_to_html(table_data)
