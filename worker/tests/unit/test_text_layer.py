"""Tests for PDF text-layer extraction, its quality gate and layout merging."""

import copy
import html
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.pipeline.text_layer import (
    PageText,
    Word,
    deserialize_words,
    evaluate_text_layer,
    extract_page_text,
    fill_pruned,
    scale_words,
    serialize_words,
)

TEST_DOCS = Path(__file__).resolve().parents[1] / "integration" / "test_docs"

GOOD_TEXT = "The quick brown fox jumps over the lazy dog. " * 8


def _prose_words(count=20, height=12.0):
    words = []
    for index in range(count):
        row, col = divmod(index, 8)
        x0 = 20.0 + col * 60.0
        y0 = 50.0 + row * 24.0
        words.append(Word(x0, y0, x0 + 40.0, y0 + height, f"word{index}", row, col, 0))
    return words


def _page(words=None, text=None, width=612.0, height=792.0):
    return PageText(
        words=_prose_words() if words is None else words,
        width=width,
        height=height,
        text=GOOD_TEXT if text is None else text,
    )


def _word(text, cx, cy, half=4.0):
    return Word(cx - half, cy - half, cx + half, cy + half, text, 0, 0, 0)


def _words_for_fill():
    return [
        _word("outer", 50.0, 50.0),
        _word("innerA", 20.0, 20.0),
        _word("innerB", 30.0, 30.0),
        _word("orphan", 150.0, 150.0),
        _word("r1c1", 250.0, 50.0),
        _word("r1c2", 400.0, 50.0),
        _word("less<than", 250.0, 145.0),
        _word("amp&ersand", 300.0, 155.0),
        _word("spanword", 350.0, 230.0),
    ]


def _envelope():
    return {
        "width": 612.0,
        "height": 792.0,
        "layout_det_res": {"boxes": [{"label": "text", "coordinate": [0, 0, 1, 1]}]},
        "overall_ocr_res": {"rec_texts": ["outer"]},
        "table_res_list": [
            {
                "cell_box_list": [
                    [200, 0, 350, 100],
                    [350, 0, 500, 100],
                    [200, 100, 350, 200],
                    [200, 200, 500, 260],
                ]
            }
        ],
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_bbox": [0, 0, 100, 100],
                "block_content": "old big",
                "score": 0.9,
            },
            {
                "block_label": "text",
                "block_bbox": [10, 10, 40, 40],
                "block_content": "old small",
                "tag": "keepme",
            },
            {
                "block_label": "table",
                "block_bbox": [200, 0, 500, 200],
                "block_content": "OLD TABLE",
            },
            {"block_label": "text", "block_bbox": None, "block_content": "no bbox"},
        ],
    }


EXPECTED_TABLE_HTML = (
    "<table>"
    "<tr>"
    '<td rowspan="1" colspan="1">r1c1</td>'
    '<td rowspan="1" colspan="1">r1c2</td>'
    "</tr>"
    "<tr>"
    '<td rowspan="1" colspan="1">less&lt;than amp&amp;ersand</td>'
    "</tr>"
    "<tr>"
    '<td rowspan="1" colspan="2">spanword</td>'
    "</tr>"
    "</table>"
)


class TestRealPdfTextLayer:
    @pytest.mark.parametrize(
        "filename,page_number,expected_words",
        [
            ("sample.pdf", 1, 217),
            ("sample.pdf", 2, 3),
            ("one_page.pdf", 1, 27),
            ("three_page.pdf", 1, 16),
            ("three_page.pdf", 2, 16),
            ("three_page.pdf", 3, 16),
        ],
    )
    def test_word_counts_and_gate(self, filename, page_number, expected_words):
        data = (TEST_DOCS / filename).read_bytes()
        page_text = extract_page_text(data, page_number)

        assert len(page_text.words) == expected_words
        assert page_text.width == pytest.approx(612.0)
        assert page_text.height == pytest.approx(792.0)
        assert len(page_text.text.strip()) > 0

        decision = evaluate_text_layer(page_text)
        if expected_words < settings.TEXT_LAYER_MIN_WORDS:
            assert decision.usable is False
            assert "min_words" in decision.reason
        else:
            assert decision.usable is True, decision.reason

    def test_extraction_reads_pdf_points_and_word_metadata(self):
        page_text = extract_page_text((TEST_DOCS / "sample.pdf").read_bytes(), 1)
        first = page_text.words[0]

        assert first.text == "CS"
        assert (first.block_no, first.line_no, first.word_no) == (2, 0, 0)
        assert first.x0 == pytest.approx(182.58203125)
        assert first.x1 > first.x0
        assert first.y1 > first.y0
        assert "CS 40700 Sprint" in page_text.text

    def test_extraction_is_bounded_by_page_number(self):
        data = (TEST_DOCS / "one_page.pdf").read_bytes()
        assert extract_page_text(data, 2).words == []
        assert extract_page_text(b"not a pdf", 1).words == []


class TestQualityGate:
    def test_thresholds_are_configured(self):
        assert settings.TEXT_LAYER_MIN_WORDS == 12
        assert settings.TEXT_LAYER_MIN_ALPHA_CHARS == 40
        assert settings.TEXT_LAYER_MIN_ALPHA_RATIO == 0.60
        assert settings.TEXT_LAYER_MAX_PRIVATE_USE_RATIO == 0.01
        assert settings.TEXT_LAYER_MAX_SYMBOL_RATIO == 0.05
        assert settings.TEXT_LAYER_MAX_CONTROL_RATIO == 0.01
        assert settings.TEXT_LAYER_MIN_IN_PAGE_RATIO == 0.95
        assert settings.TEXT_LAYER_WORD_HEIGHT_MIN_PT == 6.0
        assert settings.TEXT_LAYER_WORD_HEIGHT_MAX_PT == 40.0

    def test_ordinary_prose_passes(self):
        decision = evaluate_text_layer(_page())
        assert decision.usable is True, decision.reason
        assert decision.reason

    def test_rejects_empty_words_and_text(self):
        no_words = evaluate_text_layer(PageText([], 612.0, 792.0, GOOD_TEXT))
        assert no_words.usable is False
        assert "no_words" in no_words.reason

        no_text = evaluate_text_layer(_page(text="  \n  "))
        assert no_text.usable is False
        assert "no_text" in no_text.reason

    def test_rejects_too_few_words(self):
        decision = evaluate_text_layer(_page(words=_prose_words(3)))
        assert decision.usable is False
        assert "min_words" in decision.reason

    def test_rejects_too_few_alpha_chars(self):
        words = _prose_words(15)
        decision = evaluate_text_layer(_page(words=words, text="a " * 15))
        assert decision.usable is False
        assert "min_alpha_chars" in decision.reason

    def test_rejects_low_alpha_ratio(self):
        decision = evaluate_text_layer(_page(text="1234567890 " * 40))
        assert decision.usable is False
        assert "min_alpha_ratio" in decision.reason

    def test_rejects_private_use_junk(self):
        decision = evaluate_text_layer(_page(text=GOOD_TEXT + "\ue000" * 30))
        assert decision.usable is False
        assert "max_private_use_ratio" in decision.reason

    def test_rejects_symbol_heavy_text(self):
        decision = evaluate_text_layer(_page(text=GOOD_TEXT + "€" * 40))
        assert decision.usable is False
        assert "max_symbol_ratio" in decision.reason

    def test_rejects_control_chars(self):
        decision = evaluate_text_layer(_page(text=GOOD_TEXT + "\x01" * 20))
        assert decision.usable is False
        assert "max_control_ratio" in decision.reason

    def test_rejects_out_of_page_boxes(self):
        words = _prose_words(20)
        for index in range(4):
            word = words[index]
            words[index] = Word(-100.0, -100.0, -80.0, -80.0, word.text, 0, 0, 0)
        decision = evaluate_text_layer(_page(words=words))
        assert decision.usable is False
        assert "min_in_page_ratio" in decision.reason

    def test_rejects_identical_boxes(self):
        words = [
            Word(10.0, 10.0, 30.0, 22.0, f"word{index}", 0, 0, index)
            for index in range(20)
        ]
        decision = evaluate_text_layer(_page(words=words))
        assert decision.usable is False
        assert "identical_word_boxes" in decision.reason

    @pytest.mark.parametrize(
        "height,valid", [(60.0, False), (2.0, False), (12.0, True)]
    )
    def test_word_height_median(self, height, valid):
        decision = evaluate_text_layer(_page(words=_prose_words(20, height=height)))
        assert decision.usable is valid
        if not valid:
            assert "median_word_height" in decision.reason


class TestWordCoordinates:
    def test_scale_words_uses_150_over_72(self):
        words = _prose_words(5)
        scaled = scale_words(_page(words=words))
        factor = 150 / 72
        for original, result in zip(words, scaled):
            assert result.x0 == pytest.approx(original.x0 * factor)
            assert result.y0 == pytest.approx(original.y0 * factor)
            assert result.x1 == pytest.approx(original.x1 * factor)
            assert result.y1 == pytest.approx(original.y1 * factor)
            assert result.text == original.text
            assert (
                result.block_no,
                result.line_no,
                result.word_no,
            ) == (original.block_no, original.line_no, original.word_no)

    def test_scale_words_honours_custom_dpi(self):
        words = _prose_words(3)
        assert scale_words(_page(words=words), dpi=72) == words

    def test_serialize_round_trip_is_json_safe(self):
        words = _prose_words(6)
        payload = serialize_words(words)
        assert isinstance(payload, dict)
        assert json.loads(json.dumps(payload)) == payload
        assert deserialize_words(payload) == words

    def test_deserialize_empty_payload(self):
        assert deserialize_words({}) == []
        assert deserialize_words({"words": []}) == []


class TestFillPruned:
    def test_fills_blocks_tables_and_preserves_everything_else(self):
        pruned = _envelope()
        snapshot = copy.deepcopy(pruned)
        words = _words_for_fill()

        result = fill_pruned(pruned, words)

        assert result is not pruned
        assert pruned == snapshot
        assert result["width"] == snapshot["width"]
        assert result["height"] == snapshot["height"]
        for key in ("layout_det_res", "overall_ocr_res", "table_res_list"):
            assert result[key] == snapshot[key]
            assert result[key] is not snapshot[key]

        blocks = result["parsing_res_list"]
        assert blocks[0]["block_content"] == "outer"
        assert blocks[0]["score"] == 0.9
        assert "innerA" not in blocks[0]["block_content"]

        assert blocks[1]["block_content"] == "innerA innerB"
        assert blocks[1]["tag"] == "keepme"

        assert blocks[2]["block_content"] == EXPECTED_TABLE_HTML
        assert blocks[3]["block_content"] == "no bbox"

    def test_word_centre_decides_block_membership(self):
        pruned = {
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [0, 0, 100, 100],
                    "block_content": "old",
                }
            ]
        }
        words = [
            _word("inside", 50.0, 50.0),
            _word("outside", 120.0, 50.0),
        ]
        result = fill_pruned(pruned, words)
        assert result["parsing_res_list"][0]["block_content"] == "inside"

    def test_no_cell_boxes_leaves_table_content_untouched(self):
        for table_result in ({}, {"cell_box_list": []}, {"cell_box_list": [None, 1]}):
            pruned = {
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_bbox": [0, 0, 100, 100],
                        "block_content": "ORIGINAL",
                    }
                ],
                "table_res_list": [table_result],
            }
            result = fill_pruned(pruned, _words_for_fill())
            assert result["parsing_res_list"][0]["block_content"] == "ORIGINAL"

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"parsing_res_list": "not-a-list"},
            {
                "parsing_res_list": [
                    None,
                    42,
                    {
                        "block_label": "text",
                        "block_bbox": [1, 2, 3],
                        "block_content": "keep",
                    },
                ]
            },
            {
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_bbox": [0, 0, 10, 10],
                        "block_content": "keep",
                    }
                ],
                "table_res_list": [{"cell_box_list": [None, [1], "junk"]}],
            },
        ],
    )
    def test_malformed_envelope_is_returned_unchanged(self, bad):
        snapshot = copy.deepcopy(bad)
        result = fill_pruned(bad, _words_for_fill())
        assert result == snapshot
        assert result is not bad

    def test_non_word_objects_are_ignored(self):
        pruned = {
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_bbox": [0, 0, 100, 100],
                    "block_content": "keep",
                }
            ]
        }
        result = fill_pruned(pruned, ["not a word", None, 42])
        assert result["parsing_res_list"][0]["block_content"] == ""

    def test_cell_text_is_escaped(self):
        assert html.escape("less<than amp&ersand") == "less&lt;than amp&amp;ersand"
