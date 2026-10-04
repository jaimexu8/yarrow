"""Renderer tests. Does not depend on the database or fixtures."""

from app.schemas.table import TableCellNode
from app.services.markdown import (
    RenderOptions,
    render_markdown,
    render_plain_text,
)
from tests.factories import BOX, page, region, simple_table, stitched_table, tree


class TestHeadings:
    def test_doc_title_and_paragraph_title_become_levels(self):
        document = tree(
            [
                page(
                    1,
                    region("doc_title", text="Annual Report"),
                    region("paragraph_title", text="Overview"),
                    region("text", text="Body copy."),
                )
            ]
        )
        assert "# Annual Report" in render_markdown(document)
        assert "## Overview" in render_markdown(document)

    def test_unknown_labels_are_kept_as_paragraphs(self):
        """An unseen OCR label must not drop content or raise."""
        document = tree([page(1, region("some_new_label", text="Do not lose me."))])
        assert "Do not lose me." in render_markdown(document)

    def test_empty_regions_are_skipped(self):
        document = tree([page(1, region("text", text="   "), region("text"))])
        assert render_markdown(document) == ""


class TestArtifacts:
    def test_headers_and_footers_are_excluded_when_asked(self):
        document = tree(
            [
                page(
                    1,
                    region("header", text="CONFIDENTIAL"),
                    region("text", text="Real content."),
                    region("footer", text="Page 1 of 9"),
                    region("number", text="1"),
                )
            ]
        )
        exported = render_markdown(document, RenderOptions(include_artifacts=False))
        assert "Real content." in exported
        assert "CONFIDENTIAL" not in exported
        assert "Page 1 of 9" not in exported

    def test_headers_are_kept_for_the_viewer(self):
        document = tree([page(1, region("header", text="CONFIDENTIAL"))])
        assert "CONFIDENTIAL" in render_markdown(
            document, RenderOptions(include_artifacts=True)
        )


class TestFigures:
    def test_figure_without_bytes_renders_a_placeholder_not_an_image(self):
        """RegionImage rows are never written, so an image link would always break."""
        document = tree([page(2, region("figure"))])
        rendered = render_markdown(document)
        assert "![" not in rendered
        assert "page 2" in rendered

    def test_caption_is_included(self):
        document = tree([page(1, region("chart", caption="Revenue by quarter"))])
        assert "Revenue by quarter" in render_markdown(document)


class TestTables:
    def test_renders_gfm_with_a_header_separator(self):
        table = simple_table([["Region", "Value"], ["East", "1200"]])
        document = tree(
            [
                page(
                    1,
                    region(
                        "table",
                        table_id=table.id,
                        region_table_id=table.parts[0].region_table_id,
                        is_first_table_part=True,
                    ),
                )
            ],
            [table],
        )
        rendered = render_markdown(document)
        assert "| Region | Value |" in rendered
        assert "| --- | --- |" in rendered
        assert "| East | 1200 |" in rendered

    def test_pipes_and_newlines_in_cells_are_escaped(self):
        table = simple_table([["a|b", "c\nd"]], header=False)
        document = tree(
            [
                page(
                    1,
                    region(
                        "table",
                        table_id=table.id,
                        region_table_id=table.parts[0].region_table_id,
                    ),
                )
            ],
            [table],
        )
        rendered = render_markdown(document)
        assert r"a\|b" in rendered
        # The row must stay on one line, or the table ends early.
        assert "c d" in rendered

    def test_spanned_cells_are_expanded_across_every_position(self):
        table = simple_table([["x", "y"], ["a", "b"]], header=False)
        table.cells = [
            TableCellNode(
                row=0,
                col=0,
                row_span=1,
                col_span=2,
                text="spans two",
                is_header=True,
                bbox=BOX,
                page_number=1,
                region_table_id=table.parts[0].region_table_id,
            ),
            TableCellNode(
                row=1,
                col=0,
                row_span=1,
                col_span=1,
                text="a",
                is_header=False,
                bbox=BOX,
                page_number=1,
                region_table_id=table.parts[0].region_table_id,
            ),
        ]
        table.has_spans = True
        document = tree(
            [
                page(
                    1,
                    region(
                        "table",
                        table_id=table.id,
                        region_table_id=table.parts[0].region_table_id,
                    ),
                )
            ],
            [table],
        )
        # GFM has no colspan, so the value is repeated rather than leaving a hole.
        assert "| spans two | spans two |" in render_markdown(document)

    def test_caption_region_suppresses_the_duplicate_stored_title(self):
        table = simple_table([["a"]], header=False, title="Table 1: Figures")
        document = tree(
            [
                page(
                    1,
                    region("table_title", text="Table 1: Figures"),
                    region(
                        "table",
                        table_id=table.id,
                        region_table_id=table.parts[0].region_table_id,
                    ),
                )
            ],
            [table],
        )
        assert render_markdown(document).count("Table 1: Figures") == 1


class TestStitchedTables:
    def _document(self):
        table, part_ids = stitched_table(
            [
                (1, [["Region", "Value"], ["East", "1"]]),
                (2, [["West", "2"]]),
                (3, [["North", "3"]]),
            ]
        )
        pages = [
            page(
                1,
                region(
                    "table",
                    table_id=table.id,
                    region_table_id=part_ids[0],
                    is_first_table_part=True,
                ),
            ),
            page(2, region("table", table_id=table.id, region_table_id=part_ids[1])),
            page(3, region("table", table_id=table.id, region_table_id=part_ids[2])),
        ]
        return tree(pages, [table]), table

    def test_emitted_once_with_rows_in_page_order(self):
        document, table = self._document()
        rendered = render_markdown(document)

        # One header separator means one table, not three fragments.
        assert rendered.count("| --- | --- |") == 1
        assert rendered.index("East") < rendered.index("West") < rendered.index("North")
        assert table.row_count == 4

    def test_records_which_page_each_row_range_came_from(self):
        document, _ = self._document()
        rendered = render_markdown(document)
        assert "rows 0-1 from page 1" in rendered
        assert "rows 2-2 from page 2" in rendered

    def test_page_filter_marks_the_table_as_continued(self):
        """When the first part is outside the requested page, say so."""
        document, _table = self._document()
        document.pages = [document.pages[1]]  # page 2 only
        document.pages_included = [2]
        rendered = render_markdown(document)
        assert "continued from page 1" in rendered
        assert "| --- | --- |" in rendered


class TestLists:
    def test_bullets_and_numbers_become_markdown(self):
        document = tree(
            [
                page(
                    1,
                    region("text", text="• first\n• second"),
                    region("text", text="1. alpha\n2) beta"),
                )
            ]
        )
        rendered = render_markdown(document)
        assert "- first" in rendered
        assert "- second" in rendered
        assert "1. alpha" in rendered
        assert "2. beta" in rendered

    def test_a_paragraph_starting_with_a_digit_is_not_a_list(self):
        document = tree([page(1, region("text", text="2024 was a strong year."))])
        assert "2024 was a strong year." in render_markdown(document)
        assert "- 2024" not in render_markdown(document)


class TestPlainText:
    def test_titles_are_underlined_and_tables_are_aligned(self):
        table = simple_table([["Region", "Value"], ["East", "1200"]])
        document = tree(
            [
                page(
                    1,
                    region("doc_title", text="Report"),
                    region(
                        "table",
                        table_id=table.id,
                        region_table_id=table.parts[0].region_table_id,
                    ),
                )
            ],
            [table],
        )
        rendered = render_plain_text(document)
        assert "Report\n======" in rendered
        assert "|" not in rendered
        assert "Region" in rendered and "East" in rendered


class TestEmptyDocuments:
    def test_no_pages_renders_nothing(self):
        assert render_markdown(tree([])) == ""
        assert render_plain_text(tree([])) == ""

    def test_failed_page_with_no_regions_is_skipped(self):
        blank = page(1)
        blank.status = "failed"
        blank.error_message = "inference timed out"
        assert render_markdown(tree([blank])) == ""
