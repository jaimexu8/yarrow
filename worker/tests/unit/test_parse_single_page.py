"""Unit tests for DocumentParser.parse_single_page, the per-page parse
entry point of the Phase 2 Celery migration.

The OCR gateway is stubbed at requests.post with OK_RESPONSE-style bodies
and orientation detection is stubbed
per instance, so the tests run with no network and no live cluster.
"""

import base64
import io
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageDraw
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionImage,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    Warning,
)

from app.pipeline.document_parser import DocumentParser, ModelInfo

MODEL = ModelInfo("M1", "1.0")


def _page_b64(width: int, height: int, seed: int = 0) -> str:
    """A light page with dark text-like lines: sharp enough to pass the
    quality checks without a live render. The
    seed shifts the line pattern so each page has distinct bytes."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    for y in range(4 + seed * 2, height - 2, 6):
        draw.line([(3, y), (width - 3, y)], fill=(40, 40, 40), width=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _body(width, height, blocks, table_res_list=()):
    pruned = {"width": width, "height": height, "parsing_res_list": blocks}
    if table_res_list:
        pruned["table_res_list"] = list(table_res_list)
    return {"result": {"layoutParsingResults": [{"prunedResult": pruned}]}}


def _text_block(content, bbox=(5, 5, 35, 30), order=0):
    return {
        "block_label": "text",
        "block_bbox": list(bbox),
        "block_content": content,
        "block_order": order,
    }


def _table_block(html, bbox=(4, 4, 20, 16), order=0):
    return {
        "block_label": "table",
        "block_bbox": list(bbox),
        "block_content": html,
        "block_order": order,
    }


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=response)


def _gateway_response(body):
    """A requests.post replacement that answers every page with body."""

    def post(url, **kwargs):
        response = mock.Mock()
        response.status_code = 200
        response.headers = {
            "X-Yarrow-Model-Name": MODEL.name,
            "X-Yarrow-Model-Version": MODEL.version,
        }
        response.json.return_value = body
        response.raise_for_status.return_value = None
        return response

    return post


def _only(objects, cls):
    matches = [obj for obj in objects if isinstance(obj, cls)]
    assert len(matches) == 1, f"expected exactly one {cls.__name__}, got {len(matches)}"
    return matches[0]


def _warnings(objects):
    return [obj for obj in objects if isinstance(obj, Warning)]


def _parser_with_pages(count: int):
    parser = DocumentParser()
    parser.pages = [_page_b64(40, 40, seed=i) for i in range(count)]
    return parser


def _stub_orientation(parser, *, angle=0.0, corrected=None):
    """Replace correct_page on the parser instance; return the pages asked."""
    calls: list[str] = []

    def correct_page(page_b64):
        calls.append(page_b64)
        if corrected is not None:
            return corrected, angle, 1.0
        return page_b64, 0.0, 0.0

    parser.orientation_corrector.correct_page = correct_page
    return calls


@pytest.fixture
def document():
    return Document(id=uuid4())


@pytest.fixture
def gateway(monkeypatch):
    outcomes: dict[str, object] = {}
    sent: list[str] = []

    def post(url, **kwargs):
        page = kwargs["json"]["file"]
        sent.append(page)
        outcome = outcomes[page]
        if isinstance(outcome, BaseException):
            raise outcome
        return _gateway_response(outcome)(url, **kwargs)

    monkeypatch.setattr("app.pipeline.document_parser.requests.post", post)
    return SimpleNamespace(sent=sent, outcomes=outcomes)


class TestParseSinglePageSuccess:
    def test_returns_one_completed_page_with_model(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40, 40, [_text_block("hello", (5, 5, 35, 30), 0)]
        )

        objects = parser.parse_single_page(document, 0)

        page = _only(objects, Page)
        assert page.document_id == document.id
        assert page.page_number == 1
        assert page.status == "completed"
        assert page.error_code is None
        assert page.error_message is None
        assert (page.model_name, page.model_version) == (MODEL.name, MODEL.version)
        assert page.width == 40
        assert page.height == 40
        assert page.page_image_key is None
        assert parser.page_models == {0: MODEL}
        # The page's result is folded into parsed_result, so the whole-document
        # helpers (get_pruned_result) keep working after per-page parses.
        assert len(parser.parsed_result) == 1
        assert (
            parser.get_pruned_result()[0]["parsing_res_list"][0]["block_content"]
            == "hello"
        )

    def test_only_the_requested_page_is_sent_to_the_gateway(self, document, gateway):
        parser = _parser_with_pages(3)
        _stub_orientation(parser)
        for page in parser.pages:
            gateway.outcomes[page] = _body(
                40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
            )

        parser.parse_single_page(document, 1)

        assert gateway.sent == [parser.pages[1]]
        # parsed_result holds one slot per page index, so parsing page 2 of a
        # 3-page document pads slots 0..1; the other slots stay empty until
        # their pages are parsed.
        assert len(parser.parsed_result) == 2
        assert (
            parser.get_pruned_result()[1]["parsing_res_list"][0]["block_content"] == "x"
        )

    def test_repeated_calls_keep_each_page_independent(self, document, gateway):
        parser = _parser_with_pages(2)
        _stub_orientation(parser)
        for page in parser.pages:
            gateway.outcomes[page] = _body(
                40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
            )

        first = parser.parse_single_page(document, 0)
        second = parser.parse_single_page(document, 1)

        first_page = _only(first, Page)
        second_page = _only(second, Page)
        assert first_page.page_number == 1
        assert second_page.page_number == 2
        assert first_page.id != second_page.id
        assert len(parser.parsed_result) == 2
        assert (
            parser.get_pruned_result()[0]["parsing_res_list"][0]["block_content"] == "x"
        )
        assert (
            parser.get_pruned_result()[1]["parsing_res_list"][0]["block_content"] == "x"
        )

    def test_regions_built_in_reading_order_with_text(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40,
            40,
            [
                _text_block("first", (5, 5, 35, 15), 0),
                _text_block("second", (5, 25, 35, 35), 1),
            ],
        )

        objects = parser.parse_single_page(document, 0)

        page = _only(objects, Page)
        regions = [obj for obj in objects if isinstance(obj, Region)]
        assert [region.reading_order for region in regions] == [0, 1]
        assert all(region.page_id == page.id for region in regions)
        assert all(region.page_number == 1 for region in regions)
        texts = {
            text.region_id: text
            for text in (obj for obj in objects if isinstance(obj, RegionText))
        }
        assert [texts[region.id].text_content for region in regions] == [
            "first",
            "second",
        ]
        # The page comes first, then warnings, then the content objects.
        assert objects[0] is page
        content = objects[1:]
        assert all(not isinstance(obj, (Page, Warning)) for obj in content)
        assert sum(isinstance(obj, Region) for obj in content) == 2
        assert sum(isinstance(obj, RegionText) for obj in content) == 2


class TestParseSinglePageRotation:
    def test_auto_rotation_stores_corrected_image_and_warns(self, document, gateway):
        parser = _parser_with_pages(1)
        corrected = _page_b64(40, 40, seed=7)
        assert corrected != parser.pages[0]
        _stub_orientation(parser, angle=90.0, corrected=corrected)
        gateway.outcomes[parser.pages[0]] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        objects = parser.parse_single_page(document, 0)

        page = _only(objects, Page)
        key = f"documents/{document.id}/pages/1.png"
        assert page.page_image_key == key
        assert parser.page_images == {key: base64.b64decode(corrected)}
        assert parser.page_orientations == {0: 90.0}
        warnings = _warnings(objects)
        assert [warning.message for warning in warnings] == [
            "Page was auto-rotated by 90°"
        ]
        assert all(
            warning.warning_type == "quality" and warning.page_id == page.id
            for warning in warnings
        )

    def test_corrected_page_is_what_the_gateway_receives(self, document, gateway):
        parser = _parser_with_pages(1)
        corrected = _page_b64(40, 40, seed=7)
        assert corrected != parser.pages[0]
        _stub_orientation(parser, angle=180.0, corrected=corrected)
        gateway.outcomes[corrected] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        parser.parse_single_page(document, 0)

        assert gateway.sent == [corrected]

    def test_upright_page_has_no_rotation_warning(self, document, gateway):
        parser = _parser_with_pages(1)
        calls = _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        objects = parser.parse_single_page(document, 0)

        assert calls == [parser.pages[0]]
        page = _only(objects, Page)
        assert page.page_image_key is None
        assert parser.page_images == {}
        assert _warnings(objects) == []

    def test_manual_override_skips_auto_detection(self, document, gateway):
        parser = _parser_with_pages(1)
        calls = _stub_orientation(parser)
        parser.manual_rotations = {0: 270}
        # The manual rotation was already applied to the raster by ingest
        # (apply_manual_rotations), so the page bytes here are the rotated
        # ones; the gateway is keyed on them.
        parser.pages[0] = _page_b64(40, 40, seed=9)
        gateway.outcomes[parser.pages[0]] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        objects = parser.parse_single_page(document, 0)

        assert calls == []  # auto-detection stayed out
        page = _only(objects, Page)
        assert page.manual_rotation_deg == 270
        assert page.page_image_key == f"documents/{document.id}/pages/1.png"
        assert parser.page_images != {}
        assert [warning.message for warning in _warnings(objects)] == [
            "Page was manually rotated by 270°"
        ]
        assert parser.page_orientations == {}

    def test_manual_override_zero_keeps_original_orientation(self, document, gateway):
        parser = _parser_with_pages(1)
        calls = _stub_orientation(parser)
        parser.manual_rotations = {0: 0}
        gateway.outcomes[parser.pages[0]] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        objects = parser.parse_single_page(document, 0)

        assert calls == []
        page = _only(objects, Page)
        assert page.manual_rotation_deg == 0
        assert page.page_image_key is None
        assert parser.page_images == {}
        assert _warnings(objects) == []


class TestParseSinglePageFailure:
    def test_failed_page_records_code_and_message(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = requests.Timeout("gateway took 60s")

        objects = parser.parse_single_page(document, 0)

        page = _only(objects, Page)
        assert page.status == "failed"
        assert page.error_code == "inference_timeout"
        assert page.error_message == "Timeout: gateway took 60s"
        assert page.model_name is None
        assert page.model_version is None
        assert parser.page_models == {}
        # A failed page yields no content objects at all.
        assert all(isinstance(obj, (Page, Warning)) for obj in objects)

    def test_http_503_maps_to_inference_unavailable(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _http_error(503)

        objects = parser.parse_single_page(document, 0)

        page = _only(objects, Page)
        assert page.status == "failed"
        assert page.error_code == "inference_unavailable"
        assert "503" in page.error_message

    def test_failure_does_not_block_other_pages(self, document, gateway):
        parser = _parser_with_pages(2)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = requests.ConnectionError("refused")
        gateway.outcomes[parser.pages[1]] = _body(
            40, 40, [_text_block("x", (5, 5, 35, 30), 0)]
        )

        first = parser.parse_single_page(document, 0)
        second = parser.parse_single_page(document, 1)

        assert _only(first, Page).status == "failed"
        assert _only(second, Page).status == "completed"
        assert len(parser.parsed_result) == 2


TABLE_HTML = (
    "<table><tr><th>H1</th><td>a1</td></tr><tr><td>b1</td><td>b2</td></tr></table>"
)


class TestParseSinglePageTables:
    def test_each_region_table_is_a_standalone_table(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40,
            40,
            [
                _table_block(TABLE_HTML, (4, 4, 20, 10), 0),
                _table_block(TABLE_HTML, (24, 4, 40, 10), 1),
            ],
            table_res_list=[
                {"cell_box_list": [[0.0, 0.0, 8.0, 3.0]] * 4},
                {"cell_box_list": [[0.0, 0.0, 8.0, 3.0]] * 4},
            ],
        )

        objects = parser.parse_single_page(document, 0)

        tables = [obj for obj in objects if isinstance(obj, Table)]
        region_tables = [obj for obj in objects if isinstance(obj, RegionTable)]
        cells = [obj for obj in objects if isinstance(obj, TableCell)]
        assert len(tables) == 2
        assert all(table.is_stitched is False for table in tables)
        assert all(table.document_id == document.id for table in tables)
        for table in tables:
            assert table.row_count == 2
            assert table.col_count == 2
        # One region table per region table, each part index 0 of its own
        # (standalone) table; the page-wide reading order lives on the regions.
        assert [region_table.reading_order for region_table in region_tables] == [0, 0]
        regions = [obj for obj in objects if isinstance(obj, Region)]
        table_regions = [region for region in regions if region.region_type == "table"]
        assert {region_table.region_id for region_table in region_tables} == {
            region.id for region in table_regions
        }
        assert sorted(region.reading_order for region in table_regions) == [
            region.reading_order for region in table_regions
        ]
        assert all(
            region_table.table_id in {table.id for table in tables}
            for region_table in region_tables
        )
        # Every cell belongs to exactly one of the two region tables.
        assert len(cells) == 8
        assert {cell.region_table_id for cell in cells} == {
            region_table.id for region_table in region_tables
        }

    def test_cells_pair_with_explicit_boxes(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        boxes = [
            [0.0, 0.0, 8.0, 3.0],
            [8.0, 0.0, 16.0, 3.0],
            [0.0, 3.0, 8.0, 6.0],
            [8.0, 3.0, 16.0, 6.0],
        ]
        gateway.outcomes[parser.pages[0]] = _body(
            40,
            40,
            [_table_block(TABLE_HTML, (4, 4, 20, 16), 0)],
            table_res_list=[{"cell_box_list": boxes}],
        )

        objects = parser.parse_single_page(document, 0)

        cells = [obj for obj in objects if isinstance(obj, TableCell)]
        assert len(cells) == 4
        by_position = {(cell.row_idx, cell.col_idx): cell for cell in cells}
        assert (by_position[(0, 0)].x0, by_position[(0, 0)].y0) == (0.0, 0.0)
        assert by_position[(0, 0)].is_header is True
        assert by_position[(1, 1)].is_header is False
        assert (by_position[(1, 1)].x0, by_position[(1, 1)].y0) == (8.0, 3.0)
        assert by_position[(0, 0)].text_content == "H1"
        assert by_position[(1, 1)].text_content == "b2"

    def test_cells_fall_back_to_uniform_grid_without_boxes(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40,
            40,
            [_table_block(TABLE_HTML, (4, 4, 20, 16), 0)],
            table_res_list=[{}],
        )

        objects = parser.parse_single_page(document, 0)

        cells = [obj for obj in objects if isinstance(obj, TableCell)]
        assert len(cells) == 4
        by_position = {(cell.row_idx, cell.col_idx): cell for cell in cells}
        # Uniform edges of the table bbox: cols at 4/12/20, rows at 4/10/16.
        assert by_position[(0, 0)].x0 == 4.0
        assert by_position[(0, 1)].x0 == 12.0
        assert by_position[(1, 0)].y0 == 10.0
        assert by_position[(1, 1)].x1 == 20.0


class TestParseSinglePageFigures:
    def test_figure_crop_lands_in_figure_images(self, document, gateway):
        parser = _parser_with_pages(1)
        _stub_orientation(parser)
        gateway.outcomes[parser.pages[0]] = _body(
            40,
            40,
            [
                _text_block("caption", (5, 5, 35, 8), 0),
                {
                    "block_label": "figure",
                    "block_bbox": [10, 10, 30, 30],
                    "block_content": "",
                    "block_order": 1,
                },
            ],
        )

        objects = parser.parse_single_page(document, 0)

        images = [obj for obj in objects if isinstance(obj, RegionImage)]
        assert len(images) == 1
        image = images[0]
        key = f"documents/{document.id}/figures/{image.region_id}.png"
        assert image.image_key == key
        assert key in parser.figure_images
        crop = Image.open(io.BytesIO(parser.figure_images[key]))
        assert crop.size == (20, 20)
        region = next(
            obj
            for obj in objects
            if isinstance(obj, Region) and obj.id == image.region_id
        )
        assert region.region_type == "figure"


class TestParseSinglePageProperties:
    @given(
        table_count=st.integers(min_value=1, max_value=4),
        rows=st.integers(min_value=1, max_value=5),
        cols=st.integers(min_value=1, max_value=5),
    )
    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_per_page_tables_are_standalone_and_in_reading_order(
        self, table_count, rows, cols
    ):
        """For any single page, each region table becomes its own Table with
        is_stitched=False, one RegionTable per table in reading order, and
        the expected number of cells per table."""
        cell_html = "".join(
            "<tr>" + "".join(f"<td>c{r}{c}</td>" for c in range(cols)) + "</tr>"
            for r in range(rows)
        )
        html = f"<table>{cell_html}</table>"

        def build():
            parser = DocumentParser()
            parser.pages = [_page_b64(40, 40)]
            parser.orientation_corrector.correct_page = lambda page_b64: (
                page_b64,
                0.0,
                0.0,
            )
            blocks = [
                _table_block(html, (2 + 9 * i, 4, 8 + 9 * i, 16), i)
                for i in range(table_count)
            ]
            with mock.patch(
                "app.pipeline.document_parser.requests.post",
                _gateway_response(_body(40, 40, blocks)),
            ):
                document = Document(id=uuid4())
                return document, parser.parse_single_page(document, 0)

        document, objects = build()
        tables = [obj for obj in objects if isinstance(obj, Table)]
        region_tables = [obj for obj in objects if isinstance(obj, RegionTable)]
        cells = [obj for obj in objects if isinstance(obj, TableCell)]

        # One standalone table per region table, never stitched within a page.
        assert len(tables) == table_count
        assert all(table.is_stitched is False for table in tables)
        assert all(table.document_id == document.id for table in tables)
        # The page-wide reading order is on the regions; each region table is
        # part index 0 of its own (standalone) table.
        regions = [obj for obj in objects if isinstance(obj, Region)]
        table_regions = [region for region in regions if region.region_type == "table"]
        assert len(table_regions) == table_count
        assert [region.reading_order for region in table_regions] == sorted(
            region.reading_order for region in table_regions
        )
        assert [region_table.reading_order for region_table in region_tables] == [
            0
        ] * table_count
        for region_table in region_tables:
            assert region_table.row_start == 0
            assert region_table.row_end == max(rows - 1, 0)
            assert region_table.col_start == 0
            assert region_table.col_end == max(cols - 1, 0)
        assert {region_table.region_id for region_table in region_tables} == {
            region.id for region in table_regions
        }
        # Each table's part holds rows * cols cells.
        assert len(cells) == table_count * rows * cols
        cells_by_region_table: dict = {}
        for cell in cells:
            cells_by_region_table.setdefault(cell.region_table_id, []).append(cell)
        assert len(cells_by_region_table) == table_count
        for region_table_id, table_cells in cells_by_region_table.items():
            assert len(table_cells) == rows * cols
            assert region_table_id in {
                region_table.id for region_table in region_tables
            }
