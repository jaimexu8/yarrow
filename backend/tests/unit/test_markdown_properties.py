"""Property-based tests for the pure markdown renderer and its table helpers.

Covers ``_build_grid`` (grid dimensions and cell placement), the GFM pipe
escaping (``_escape_cell``) via a render/roundtrip, the CSV export (BOM,
table separation), and ``_normalize_lists`` (line count). The module imports
schemas but performs no I/O, so these run without a database.
"""

import csv
import io
import re
from datetime import UTC, datetime
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from app.schemas.common import BBox
from app.schemas.document import DocumentOut
from app.schemas.parsed import (
    DocumentStats,
    DocumentTree,
    PageNode,
    RegionNode,
)
from app.schemas.table import (
    RowSource,
    TableCellNode,
    TableNode,
    TablePart,
)
from app.services.markdown import (
    _build_grid,
    _escape_cell,
    _normalize_lists,
    render_csv,
    render_markdown,
)

BOX = BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

# Cell text may contain the characters that would break a GFM table or a CSV
# row: pipes, newlines, backslashes, plus ordinary and unicode characters.
cell_text_strategy = st.text(
    st.sampled_from(["a", "b", "c", "|", "\n", "\\", " ", "\u00e9", "z"]),
    min_size=0,
    max_size=8,
)


def make_cell(
    row: int, col: int, row_span: int, col_span: int, text: str | None
) -> TableCellNode:
    return TableCellNode(
        id=uuid4(),
        row=row,
        col=col,
        row_span=row_span,
        col_span=col_span,
        text=text,
        is_header=row == 0,
        bbox=BOX,
        page_number=1,
        region_table_id=uuid4(),
    )


def any_cell_strategy() -> st.SearchStrategy[TableCellNode]:
    return st.builds(
        make_cell,
        row=st.integers(min_value=0, max_value=10),
        col=st.integers(min_value=0, max_value=10),
        row_span=st.integers(min_value=1, max_value=5),
        col_span=st.integers(min_value=1, max_value=5),
        text=st.one_of(cell_text_strategy, st.none()),
    )


def non_overlapping_cells() -> st.SearchStrategy[list[TableCellNode]]:
    """A set of 1x1 cells at distinct grid positions, each with a unique text."""
    return (
        st.lists(st.integers(min_value=0, max_value=19), min_size=0, max_size=16)
        .map(lambda coords: sorted(set(coords)))
        .map(
            lambda coords: [
                make_cell(r, c, 1, 1, f"cell-{i}")
                for i, (r, c) in enumerate((coord // 4, coord % 4) for coord in coords)
            ]
        )
    )


# --- _build_grid ------------------------------------------------------------


@given(cells=st.lists(any_cell_strategy(), min_size=1, max_size=20))
@settings(max_examples=200, deadline=None)
def test_build_grid_dimensions_equal_max_span_extent(cells):
    grid = _build_grid(cells)
    height = max(c.row + c.row_span for c in cells)
    width = max(c.col + c.col_span for c in cells)
    assert len(grid) == height
    assert all(len(row) == width for row in grid)


@given(cells=non_overlapping_cells())
@settings(max_examples=200, deadline=None)
def test_build_grid_places_cell_text_at_its_position(cells):
    if not cells:
        assert _build_grid(cells) == []
        return
    grid = _build_grid(cells)
    height = max(c.row + c.row_span for c in cells)
    width = max(c.col + c.col_span for c in cells)
    for cell in cells:
        assert grid[cell.row][cell.col] == (cell.text or "")
    # Positions no cell addresses are empty.
    addressed = {(c.row, c.col) for c in cells}
    for r in range(height):
        for c in range(width):
            if (r, c) not in addressed:
                assert grid[r][c] == ""


@given(
    cells=st.lists(any_cell_strategy(), min_size=2, max_size=20).filter(
        lambda cs: any(
            (a.row == b.row and a.col == b.col) for a in cs for b in cs if a is not b
        )
    )
)
@settings(max_examples=100, deadline=None)
def test_build_grid_never_raises_on_overlapping_cells(cells):
    grid = _build_grid(cells)  # must not raise
    assert isinstance(grid, list)


# --- GFM roundtrip (pins _escape_cell) --------------------------------------


def make_table(rows: list[list[str]]) -> TableNode:
    region_table_id = uuid4()
    cells = [
        make_cell(r, c, 1, 1, value)
        for r, row in enumerate(rows)
        for c, value in enumerate(row)
    ]
    width = max(len(row) for row in rows)
    return TableNode(
        id=uuid4(),
        title=None,
        row_count=len(rows),
        col_count=width,
        is_stitched=False,
        has_spans=False,
        first_page=1,
        last_page=1,
        parts=[
            TablePart(
                region_table_id=region_table_id,
                region_id=uuid4(),
                page_number=1,
                reading_order=0,
                row_start=0,
                row_end=len(rows) - 1,
                col_start=0,
                col_end=width - 1,
            )
        ],
        row_sources=[RowSource(row=r, page_number=1) for r in range(len(rows))],
        cells=cells,
    )


def make_tree(tables: list[TableNode]) -> DocumentTree:
    regions = [
        RegionNode(
            id=uuid4(),
            page_number=1,
            reading_order=i,
            region_type="table",
            bbox=BOX,
            text=None,
            table_id=table.id,
            region_table_id=table.parts[0].region_table_id,
        )
        for i, table in enumerate(tables)
    ]
    page = PageNode(
        page_number=1, width=10, height=10, status="completed", regions=regions
    )
    return DocumentTree(
        schema_version=1,
        generated_at=datetime.now(UTC),
        document=DocumentOut(
            id=uuid4(),
            filename="f.pdf",
            file_size_bytes=1,
            file_type="application/pdf",
            page_count=1,
            status="completed",
        ),
        pages_included=[1],
        pages=[page],
        tables=tables,
        stats=DocumentStats(
            page_count=1,
            high_confidence_page_count=0,
            region_count=len(tables),
            table_count=len(tables),
            stitched_table_count=0,
            warning_count=0,
        ),
    )


def split_gfm_row(line: str) -> list[str]:
    """Split a GFM table line on unescaped pipes and strip each cell."""
    parts = re.split(r"(?<!\\)\|", line)
    # The leading and trailing "|" produce empty ends.
    return [part.strip() for part in parts[1:-1]]


def unescape(cell: str) -> str:
    return cell.replace("\\|", "|")


@given(text=cell_text_strategy)
@settings(max_examples=200, deadline=None)
def test_escape_cell_has_no_unescaped_pipe_or_newline(text):
    out = _escape_cell(text)
    assert re.search(r"(?<!\\)\|", out) is None
    assert "\n" not in out
    assert out == out.strip()


def rectangular_rows() -> st.SearchStrategy[list[list[str]]]:
    return st.lists(
        st.lists(cell_text_strategy, min_size=1, max_size=4), min_size=1, max_size=4
    ).map(
        lambda rows: [
            row + [""] * (max(len(r) for r in rows) - len(row)) for row in rows
        ]
    )


@given(rows=rectangular_rows())
@settings(max_examples=150, deadline=None)
def test_gfm_roundtrip_recovers_cell_texts(rows):
    table = make_table(rows)
    rendered = render_markdown(make_tree([table]))

    grid = _build_grid(table.cells)
    width = len(grid[0])
    table_lines = [line for line in rendered.splitlines() if line.startswith("|")]
    assert len(table_lines) == len(grid) + 1

    for line in table_lines:
        assert len(split_gfm_row(line)) == width

    def expected(row_index: int) -> list[str]:
        return [(grid[row_index][c]).replace("\n", " ").strip() for c in range(width)]

    assert [unescape(p) for p in split_gfm_row(table_lines[0])] == expected(0)
    for row_index in range(1, len(grid)):
        assert [
            unescape(p) for p in split_gfm_row(table_lines[row_index + 1])
        ] == expected(row_index)


# --- CSV roundtrip ------------------------------------------------------------


@given(tables=st.lists(rectangular_rows(), min_size=1, max_size=4))
@settings(max_examples=100, deadline=None)
def test_csv_output_starts_with_bom_and_parses(tables):
    built = [make_table(t) for t in tables]
    output = render_csv(make_tree(built))
    assert output.startswith("\ufeff")
    parsed = list(csv.reader(io.StringIO(output[1:])))
    assert parsed is not None


@given(tables=st.lists(rectangular_rows(), min_size=2, max_size=4))
@settings(max_examples=100, deadline=None)
def test_csv_tables_separated_by_exactly_one_blank_row(tables):
    built = [make_table(t) for t in tables]
    output = render_csv(make_tree(built))
    parsed = list(csv.reader(io.StringIO(output[1:])))
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in parsed:
        if row == []:
            blocks.append(current)
            current = []
        else:
            current.append(row)
    blocks.append(current)
    assert all(block for block in blocks)
    assert len(blocks) == len(tables)


# --- _normalize_lists ---------------------------------------------------------


@given(
    lines=st.lists(
        cell_text_strategy.filter(lambda t: t.strip() != "" and "\n" not in t),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=200, deadline=None)
def test_normalize_lists_preserves_line_count(lines):
    text = "\n".join(lines)
    out = _normalize_lists(text)
    assert len(out.split("\n")) == len(text.split("\n"))


@given(text=cell_text_strategy)
@settings(max_examples=100, deadline=None)
def test_normalize_lists_never_increases_line_count(text):
    out = _normalize_lists(text)
    assert len(out.split("\n")) <= len(text.split("\n"))


def test_normalize_lists_strips_leading_and_trailing_blanks():
    assert _normalize_lists("a\nb\n").split("\n") == ["a", "b"]
    assert _normalize_lists("\na\nb").split("\n") == ["a", "b"]
    assert _normalize_lists("a\n\n\nb").split("\n") == ["a", "", "", "b"]
