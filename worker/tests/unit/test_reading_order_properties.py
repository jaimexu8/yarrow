"""Property-based tests for the pure reading-order sort.

The highest-value invariant is that the output is always a permutation of the input,
which must hold for arbitrary block lists
including the degenerate inputs that trigger the legacy fallback.
"""

import math
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.pipeline.reading_order import _legacy_order, order_blocks

# --- strategies -------------------------------------------------------------


def make_block(
    content: str, bbox: list[float], order: int | None, bid: int | None
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "block_label": "text",
        "block_bbox": bbox,
        "block_content": content,
    }
    if order is not None:
        block["block_order"] = order
    if bid is not None:
        block["block_id"] = bid
    return block


def valid_bbox() -> st.SearchStrategy[list[float]]:
    return st.lists(
        st.floats(
            min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False
        ),
        min_size=4,
        max_size=4,
    ).map(
        lambda v: [
            min(v[0], v[2]),
            min(v[1], v[3]),
            max(v[0], v[2]) + 1.0,
            max(v[1], v[3]) + 1.0,
        ]
    )


def block_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.builds(
        make_block,
        content=st.text(min_size=1, max_size=8, alphabet="ABCDEFGH"),
        bbox=valid_bbox(),
        order=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
        bid=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
    )


def block_list_strategy() -> st.SearchStrategy[list[dict[str, Any]]]:
    # Suffix each content with its index so it is unique and identity can be
    # checked by content
    return st.lists(block_strategy(), min_size=0, max_size=12).map(
        lambda blocks: [
            {**b, "block_content": f"{b['block_content']}#{i}"}
            for i, b in enumerate(blocks)
        ]
    )


page_width_strategy = st.floats(
    min_value=10.0, max_value=4000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def two_column_blocks(draw: st.DrawFn) -> tuple[list[dict[str, Any]], float]:
    """Blocks fully inside a left x-range and a right x-range, separated by an
    interior gutter wider than 2% of the page width; every block width between
    10% and 50% of the page width.

    Each column shares a single x0 so that all of its blocks overlap in x and
    merge into one coverage interval; the two columns are disjoint with a clear
    interior gap, which is what the module detects as a column gutter. Block
    width and vertical position still vary per block.
    """
    page_width = draw(
        st.floats(
            min_value=100.0, max_value=4000.0, allow_nan=False, allow_infinity=False
        )
    )
    n_left = draw(st.integers(min_value=1, max_value=4))
    n_right = draw(st.integers(min_value=1, max_value=4))

    def column_blocks(
        prefix: str, x0: float, width_lo: float, width_hi: float
    ) -> list[dict[str, Any]]:
        count = n_left if prefix == "L" else n_right
        out = []
        for i in range(count):
            width = draw(
                st.floats(
                    min_value=width_lo,
                    max_value=width_hi,
                    allow_nan=False,
                    allow_infinity=False,
                )
            )
            y0 = draw(
                st.floats(
                    min_value=0.0,
                    max_value=1500.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            )
            height = draw(
                st.floats(
                    min_value=20.0,
                    max_value=150.0,
                    allow_nan=False,
                    allow_infinity=False,
                )
            )
            out.append(
                make_block(
                    f"{prefix}{i}", [x0, y0, x0 + width, y0 + height], None, None
                )
            )
        return out

    # Left column starts at x0 in [5%, 15%] with width in [12%, 25%], so it
    # spans [5%, 40%]. Right column starts at x0 in [55%, 65%] with width in
    # [12%, 25%], so it spans [55%, 90%]. The gap between them is >= 15% > 2%,
    # and every block width is inside (10%, 50%).
    left_x0 = draw(
        st.floats(
            min_value=0.05 * page_width,
            max_value=0.15 * page_width,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    right_x0 = draw(
        st.floats(
            min_value=0.55 * page_width,
            max_value=0.65 * page_width,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    left = column_blocks("L", left_x0, 0.12 * page_width, 0.25 * page_width)
    right = column_blocks("R", right_x0, 0.12 * page_width, 0.25 * page_width)
    return [b for group in (left, right) for b in group], page_width


PROFILE = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def contents(blocks: list[dict[str, Any]]) -> list[str]:
    return [b["block_content"] for b in blocks]


# --- permutation ----------------------------------


@given(blocks=block_list_strategy(), page_width=page_width_strategy)
@PROFILE
def test_output_is_a_permutation_of_the_input(blocks, page_width):
    result = order_blocks(blocks, page_width)
    assert sorted(contents(result)) == sorted(contents(blocks))
    assert len(result) == len(blocks)


@given(blocks=block_list_strategy())
@PROFILE
def test_output_is_a_permutation_for_degenerate_page_width(blocks):
    for page_width in (0, -5, True, None):
        result = order_blocks(blocks, page_width)
        assert sorted(contents(result)) == sorted(contents(blocks))


# --- determinism ------------------------------------------------------------


@given(blocks=block_list_strategy(), page_width=page_width_strategy)
@PROFILE
def test_same_input_same_output(blocks, page_width):
    first = contents(order_blocks(blocks, page_width))
    second = contents(order_blocks(list(blocks), page_width))
    assert first == second


# --- two-column layout ------------------------------------------------------


@given(spec=two_column_blocks())
@PROFILE
def test_two_column_reads_column_major(spec):
    blocks, page_width = spec
    result = order_blocks(blocks, page_width)
    order = contents(result)
    left_positions = [i for i, c in enumerate(order) if c.startswith("L")]
    right_positions = [i for i, c in enumerate(order) if c.startswith("R")]
    assert max(left_positions) < min(right_positions)
    for prefix in ("L", "R"):
        ys = [
            b["block_bbox"][1] for b in result if b["block_content"].startswith(prefix)
        ]
        assert all(ys[i] <= ys[i + 1] for i in range(len(ys) - 1))


# --- degenerate inputs -> legacy -------------------------------------------


@given(blocks=block_list_strategy())
@PROFILE
def test_degenerate_page_width_returns_legacy(blocks):
    for page_width in (0, -5, True, None):
        assert order_blocks(blocks, page_width) == _legacy_order(blocks)


def bad_bbox() -> st.SearchStrategy[Any]:
    return st.one_of(
        # x1 <= x0 or y1 <= y0 (degenerate / inverted)
        st.tuples(
            st.floats(
                min_value=0, max_value=1000, allow_nan=False, allow_infinity=False
            ),
            st.floats(
                min_value=0, max_value=1000, allow_nan=False, allow_infinity=False
            ),
            st.floats(
                min_value=0, max_value=1000, allow_nan=False, allow_infinity=False
            ),
            st.floats(
                min_value=0, max_value=1000, allow_nan=False, allow_infinity=False
            ),
        ).filter(lambda v: v[2] <= v[0] or v[3] <= v[1]),
        # wrong length
        st.lists(
            st.floats(
                min_value=0, max_value=1000, allow_nan=False, allow_infinity=False
            ),
            min_size=2,
            max_size=5,
        ).filter(lambda v: len(v) != 4),
        # not a list
        st.one_of(st.none(), st.just("notalist"), st.integers()),
    )


@given(tail=block_list_strategy(), bad=bad_bbox())
@PROFILE
def test_bad_bbox_returns_legacy(tail, bad):
    # Inject one block with a malformed bbox among otherwise-valid blocks; the
    # whole page falls back to legacy order.
    blocks = list(tail) + [
        {
            "block_label": "text",
            "block_content": "BADBBOX",
            "block_order": 0,
            "block_id": 0,
            "block_bbox": bad,
        }
    ]
    assert order_blocks(blocks, 1700.0) == _legacy_order(blocks)


@given(n=st.integers(min_value=1, max_value=6))
@PROFILE
def test_all_full_width_returns_legacy(n):
    # Every block wider than half the page: no column can be detected, so the
    # legacy order is kept.
    page_width = 1700.0
    blocks = [
        make_block(
            f"F{i}",
            [50.0, 100.0 + i * 120.0, 50.0 + 0.6 * page_width, 160.0 + i * 120.0],
            i if i % 2 == 0 else None,
            None,
        )
        for i in range(n)
    ]
    assert order_blocks(blocks, page_width) == _legacy_order(blocks)


# --- NaN page width ---------------------------------------------------------
# The module's guard is `page_width <= 0`, which is False for NaN. So a NaN
# page width is NOT treated as degenerate: when the blocks carry a full model
# order it proceeds to a geometry (y-then-model-order) sort rather than the
# legacy order. The permutation and determinism invariants still hold.


@given(blocks=block_list_strategy())
@PROFILE
def test_nan_page_width_is_still_a_permutation_and_deterministic(blocks):
    first = contents(order_blocks(blocks, math.nan))
    second = contents(order_blocks(list(blocks), math.nan))
    assert first == second
    assert sorted(first) == sorted(contents(blocks))
