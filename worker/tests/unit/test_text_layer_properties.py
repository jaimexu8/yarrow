"""Bounded property checks for the text-layer gate and layout merging."""

import copy
from collections import defaultdict

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipeline.text_layer import PageText, Word, evaluate_text_layer, fill_pruned

PROSE = (
    "the quick brown fox jumps over a lazy dog while reading the document "
    "and writing ordinary english words into the text layer of the page"
)

NOVEL_TOKENS = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
)

JUNK = ("\ue000", "\uf123", "€", "$", "©", "♠")

FRACTION = st.floats(
    min_value=0.05,
    max_value=0.95,
    allow_nan=False,
    allow_infinity=False,
)


def _grid_boxes(count):
    boxes = []
    for index in range(count):
        row, col = divmod(index, 4)
        x0 = 10.0 + col * 120.0
        y0 = 10.0 + row * 60.0
        boxes.append([x0, y0, x0 + 100.0, y0 + 40.0])
    return boxes


@settings(max_examples=15, deadline=None)
@given(
    word_count=st.integers(min_value=12, max_value=30),
    prose=st.lists(st.sampled_from(PROSE.split()), min_size=20, max_size=60),
)
def test_ordinary_prose_always_passes(word_count, prose):
    words = []
    for index in range(word_count):
        row, col = divmod(index, 8)
        x0 = 20.0 + col * 70.0
        y0 = 50.0 + row * 24.0
        words.append(Word(x0, y0, x0 + 50.0, y0 + 12.0, "word", row, col, index))
    page = PageText(words, 612.0, 792.0, " ".join(prose))

    decision = evaluate_text_layer(page)
    assert decision.usable is True, decision.reason


@settings(max_examples=15, deadline=None)
@given(
    repeats=st.integers(min_value=2, max_value=12),
    junk=st.sampled_from(JUNK),
)
def test_injected_junk_always_rejects(repeats, junk):
    text = PROSE + junk * (len(PROSE) // (repeats + 1))
    words = []
    for index in range(20):
        row, col = divmod(index, 8)
        x0 = 20.0 + col * 60.0
        y0 = 50.0 + row * 24.0
        words.append(Word(x0, y0, x0 + 40.0, y0 + 12.0, "word", row, col, index))

    decision = evaluate_text_layer(PageText(words, 612.0, 792.0, text))
    assert decision.usable is False, decision.reason


@settings(max_examples=15, deadline=None)
@given(
    block_count=st.integers(min_value=1, max_value=8),
    word_count=st.integers(min_value=1, max_value=20),
    tokens=st.lists(st.sampled_from(NOVEL_TOKENS), min_size=20, max_size=20),
    picks=st.lists(st.integers(min_value=0, max_value=7), min_size=20, max_size=20),
    fx=st.lists(FRACTION, min_size=20, max_size=20),
    fy=st.lists(FRACTION, min_size=20, max_size=20),
)
def test_words_land_in_exactly_one_block(
    block_count, word_count, tokens, picks, fx, fy
):
    boxes = _grid_boxes(block_count)
    words = []
    assigned = defaultdict(set)
    for index in range(word_count):
        block_index = picks[index] % block_count
        box = boxes[block_index]
        cx = box[0] + fx[index] * (box[2] - box[0])
        cy = box[1] + fy[index] * (box[3] - box[1])
        text = f"w{index}_{tokens[index]}"
        words.append(Word(cx - 2.0, cy - 2.0, cx + 2.0, cy + 2.0, text, 0, 0, index))
        assigned[block_index].add(text)

    pruned = {
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_bbox": box,
                "block_content": "ORIGINAL",
            }
            for box in boxes
        ]
    }
    result = fill_pruned(pruned, words)

    seen = set()
    for block_index, block in enumerate(result["parsing_res_list"]):
        got = set(block["block_content"].split())
        assert got == assigned[block_index]
        assert not (got & seen)
        seen |= got


@settings(max_examples=15, deadline=None)
@given(
    as_text=st.booleans(),
    tokens=st.lists(st.sampled_from(NOVEL_TOKENS), min_size=1, max_size=5),
)
def test_fill_pruned_never_mutates_input(as_text, tokens):
    words = [
        Word(float(index), 0.0, float(index) + 4.0, 8.0, token, 0, 0, index)
        for index, token in enumerate(tokens)
    ]
    pruned = {
        "width": 612.0,
        "parsing_res_list": [
            {
                "block_label": "text" if as_text else "table",
                "block_bbox": [0, 0, 50, 50],
                "block_content": "ORIGINAL",
            }
        ],
        "table_res_list": [{"cell_box_list": [[0, 0, 25, 25]]}],
    }
    snapshot = copy.deepcopy(pruned)

    result = fill_pruned(pruned, words)

    assert pruned == snapshot
    assert result is not pruned
    assert result["parsing_res_list"] is not pruned["parsing_res_list"]
    assert result["table_res_list"] is not pruned["table_res_list"]
