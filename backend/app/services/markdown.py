"""Render a DocumentTree as Markdown or plain text"""

import re
from dataclasses import dataclass

from app.schemas.parsed import DocumentTree, PageNode, RegionNode
from app.schemas.table import TableCellNode, TableNode

# Region types come straight through from the OCR's block_label.
HEADING_LEVELS: dict[str, int] = {
    "doc_title": 1,
    "title": 1,
    "paragraph_title": 2,
    "section_title": 2,
    "sub_paragraph_title": 3,
}

TABLE_LABELS = frozenset({"table"})
TABLE_CAPTION_LABELS = frozenset({"table_title", "table_caption"})
FIGURE_LABELS = frozenset({"image", "figure", "chart", "seal"})
FORMULA_LABELS = frozenset({"formula", "equation"})
ARTIFACT_LABELS = frozenset({"header", "footer", "number", "page_number"})

_BULLET = re.compile(r"^\s*[•▪◦·‣∙*\-–—]\s+")
_ORDERED = re.compile(r"^\s*(\d{1,3})[.)]\s+")
_WHITESPACE_RUN = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class RenderOptions:
    include_artifacts: bool = True  # Headers, footers and page numbers. False for exports, True for the viewer.
    page_separators: bool = True  # Emit a rule and an anchor comment between pages, for scroll syncing.
    table_page_comment: bool = True  # Record which page each row range came from, above a stitched table.


def render_markdown(tree: DocumentTree, options: RenderOptions | None = None) -> str:
    return _Renderer(tree, options or RenderOptions()).markdown()


def render_plain_text(tree: DocumentTree, options: RenderOptions | None = None) -> str:
    return _Renderer(tree, options or RenderOptions()).plain_text()


class _Renderer:
    def __init__(self, tree: DocumentTree, options: RenderOptions) -> None:
        self.tree = tree
        self.options = options
        self.tables: dict[str, TableNode] = {str(t.id): t for t in tree.tables}

        # Which region a logical table is emitted at. Normally its first part,
        # but under a page filter the first part may not be in this payload, so
        # the earliest part that *is* present wins and the output says it is a
        # continuation.
        present: set[str] = {str(region.region_table_id) for page in tree.pages for region in page.regions if region.region_table_id is not None}
        self.emit_at: dict[str, str] = {}  # region_table_id -> table_id
        self.continued: set[str] = set()  # table_ids emitted from a later part
        for table in tree.tables:
            for index, part in enumerate(table.parts):
                if str(part.region_table_id) in present:
                    self.emit_at[str(part.region_table_id)] = str(table.id)
                    if index > 0:
                        self.continued.add(str(table.id))
                    break

    # --- shared traversal ---------------------------------------------------

    def _visible(self, page: PageNode) -> list[RegionNode]:
        regions = sorted(page.regions, key=lambda r: r.reading_order)
        if self.options.include_artifacts:
            return regions
        return [r for r in regions if (r.region_type or "") not in ARTIFACT_LABELS]

    def _table_for(self, region: RegionNode) -> TableNode | None:
        """The table to emit here, or None if this region is a continuation"""
        if region.region_table_id is None:
            return None
        table_id = self.emit_at.get(str(region.region_table_id))
        return None if table_id is None else self.tables.get(table_id)

    # --- markdown -----------------------------------------------------------

    def markdown(self) -> str:
        pages: list[str] = []
        for page in sorted(self.tree.pages, key=lambda p: p.page_number):
            blocks = self._markdown_page(page)
            if not blocks:
                continue
            if self.options.page_separators:
                blocks.insert(0, f"<!-- page {page.page_number} -->")
            pages.append("\n\n".join(blocks))

        separator = "\n\n---\n\n" if self.options.page_separators else "\n\n"
        body = separator.join(pages).strip()
        return f"{body}\n" if body else ""

    def _markdown_page(self, page: PageNode) -> list[str]:
        blocks: list[str] = []
        previous_was_caption = False

        for region in self._visible(page):
            label = region.region_type or ""
            text = _clean(region.text)

            if label in TABLE_LABELS:
                table = self._table_for(region)
                if table is not None:
                    blocks.extend(self._markdown_table(table, captioned=previous_was_caption))
                previous_was_caption = False
                continue

            previous_was_caption = label in TABLE_CAPTION_LABELS

            if label in TABLE_CAPTION_LABELS:
                if text:
                    blocks.append(f"**{text}**")
                continue

            if label in FIGURE_LABELS:
                blocks.append(self._markdown_figure(region))
                continue

            if label in FORMULA_LABELS:
                if text:
                    blocks.append(f"$$\n{text}\n$$")
                continue

            if not text:
                continue

            level = HEADING_LEVELS.get(label)
            if level is not None:
                blocks.append(f"{'#' * level} {text}")
            else:
                blocks.append(_normalize_lists(text))

        return blocks

    def _markdown_figure(self, region: RegionNode) -> str:
        label = (region.region_type or "figure").replace("_", " ")
        if region.image_key:
            return f"![{region.caption or label}]({region.image_key})"
        caption = f" — {region.caption}" if region.caption else ""
        return f"> _{label.capitalize()} on page {region.page_number}{caption}_"

    def _markdown_table(self, table: TableNode, captioned: bool) -> list[str]:
        blocks: list[str] = []

        # Only add the stored title when no adjacent caption region already
        # supplied one, or the same text appears twice.
        if table.title and not captioned:
            blocks.append(f"**{_clean(table.title)}**")

        if str(table.id) in self.continued:
            blocks.append(f"_(table continued from page {table.first_page})_")

        if self.options.table_page_comment and table.is_stitched:
            blocks.append(f"<!-- {_describe_row_pages(table)} -->")

        grid = _build_grid(table.cells)
        if not grid:
            return blocks

        escaped = [[_escape_cell(cell) for cell in row] for row in grid]
        header, *body = escaped
        width = len(header)
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        blocks.append("\n".join(lines))
        return blocks

    # --- plain text ---------------------------------------------------------

    def plain_text(self) -> str:
        pages: list[str] = []
        for page in sorted(self.tree.pages, key=lambda p: p.page_number):
            blocks: list[str] = []
            previous_was_caption = False

            for region in self._visible(page):
                label = region.region_type or ""
                text = _clean(region.text)

                if label in TABLE_LABELS:
                    table = self._table_for(region)
                    if table is not None:
                        if table.title and not previous_was_caption:
                            blocks.append(_clean(table.title))
                        blocks.append(_plain_table(table))
                    previous_was_caption = False
                    continue

                previous_was_caption = label in TABLE_CAPTION_LABELS

                if label in FIGURE_LABELS:
                    blocks.append(f"[{label} on page {region.page_number}]")
                    continue

                if not text:
                    continue

                level = HEADING_LEVELS.get(label)
                if level is not None:
                    rule = "=" if level == 1 else "-"
                    blocks.append(f"{text}\n{rule * len(text)}")
                else:
                    blocks.append(_normalize_lists(text))

            if blocks:
                pages.append("\n\n".join(blocks))

        body = "\n\n".join(pages).strip()
        return f"{body}\n" if body else ""


# --- helpers ----------------------------------------------------------------


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE_RUN.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _normalize_lists(text: str) -> str:
    """Best-effort list recovery.

    The OCR has no list label, so items arrive as ordinary text. This rewrites
    the common bullet and numbering forms into Markdown.
    """
    lines = []
    for line in text.split("\n"):
        indent = " " * (2 * ((len(line) - len(line.lstrip(" "))) // 2))
        if _BULLET.match(line):
            lines.append(f"{indent}- " + _BULLET.sub("", line).strip())
        elif match := _ORDERED.match(line):
            lines.append(f"{indent}{match.group(1)}. " + _ORDERED.sub("", line).strip())
        else:
            lines.append(line.strip())
    return "\n".join(lines).strip()


def _escape_cell(text: str) -> str:
    """A pipe would end the cell, and a newline would end the row"""
    return text.replace("|", r"\|").replace("\n", " ").strip()


def _build_grid(cells: list[TableCellNode]) -> list[list[str]]:
    """Lay cells out on a dense grid addressed by global row and column"""
    if not cells:
        return []

    height = max(cell.row + cell.row_span for cell in cells)
    width = max(cell.col + cell.col_span for cell in cells)
    if height <= 0 or width <= 0:
        return []

    grid = [["" for _ in range(width)] for _ in range(height)]
    for cell in cells:
        text = cell.text or ""
        for row in range(cell.row, min(cell.row + cell.row_span, height)):
            for col in range(cell.col, min(cell.col + cell.col_span, width)):
                grid[row][col] = text
    return grid


def _describe_row_pages(table: TableNode) -> str:
    """ "rows 0-16 from page 1; 17-31 from page 2" -- US-24 traceability."""
    spans = [f"rows {part.row_start}-{part.row_end} from page {part.page_number}" for part in table.parts]
    return "; ".join(spans)


def _plain_table(table: TableNode) -> str:
    grid = _build_grid(table.cells)
    if not grid:
        return ""
    cleaned = [[cell.replace("\n", " ").strip() for cell in row] for row in grid]
    widths = [min(max((len(row[col]) for row in cleaned), default=0), 40) for col in range(len(cleaned[0]))]
    lines = []
    for row in cleaned:
        cells = [(f"{value[:widths[col] - 1]}…" if len(value) > widths[col] else value).ljust(widths[col]) for col, value in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)
