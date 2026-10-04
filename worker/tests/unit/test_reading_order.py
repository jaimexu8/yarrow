"""Unit tests for the US-44 reading-order sort (pure bbox logic, no I/O)"""

from app.pipeline.reading_order import order_blocks

PAGE_W = 1700.0


def block(x0, y0, x1, y1, label="text", order=None, bid=None, content="x"):
    b = {"block_label": label, "block_bbox": [x0, y0, x1, y1], "block_content": content}
    if order is not None:
        b["block_order"] = order
    if bid is not None:
        b["block_id"] = bid
    return b


def ids(blocks):
    """Content as a stand-in for identity: blocks carry unique content here."""
    return [b["block_content"] for b in blocks]


def two_column_blocks(z_order: bool):
    """Title, LEFT-1..6, RIGHT-1..6, footer.

    When z_order is true, block_order numbers them row-major
    (L1, R1, L2, R2, ...), as a layout detector scanning rows would.
    """
    title = block(100, 80, 1600, 140, label="doc_title", content="TITLE")
    left = [
        block(120, 220 + i * 200, 760, 340 + i * 200, content=f"LEFT-{i + 1}")
        for i in range(6)
    ]
    right = [
        block(880, 220 + i * 200, 1520, 340 + i * 200, content=f"RIGHT-{i + 1}")
        for i in range(6)
    ]
    footer = block(100, 2050, 1600, 2110, label="footer", content="FOOTER")

    blocks = [title] + left + right + [footer]
    if z_order:
        sequence = [title]
        for l, r in zip(left, right):
            sequence.extend([l, r])
        sequence.append(footer)
        for i, b in enumerate(sequence):
            b["block_order"] = i
    return blocks


class TestOrderBlocks:
    def test_empty(self):
        assert order_blocks([], PAGE_W) == []

    def test_two_column_reads_column_major(self):
        # No model order: pure geometry.
        result = order_blocks(two_column_blocks(z_order=False), PAGE_W)
        assert ids(result) == ["TITLE"] + [f"LEFT-{i}" for i in range(1, 7)] + [
            f"RIGHT-{i}" for i in range(1, 7)
        ] + ["FOOTER"]

    def test_two_column_fixes_row_major_model_order(self):
        # The model enumerates row-major (Z order); the sort must regroup it.
        result = order_blocks(two_column_blocks(z_order=True), PAGE_W)
        assert ids(result) == ["TITLE"] + [f"LEFT-{i}" for i in range(1, 7)] + [
            f"RIGHT-{i}" for i in range(1, 7)
        ] + ["FOOTER"]

    def test_three_column_reads_column_major(self):
        title = block(100, 80, 1600, 140, content="TITLE")
        cols = []
        for c in range(3):
            x0 = 120 + c * 520
            for i in range(4):
                cols.append(
                    (
                        c,
                        block(
                            x0,
                            220 + i * 200,
                            x0 + 400,
                            320 + i * 200,
                            content=f"C{c}-P{i}",
                        ),
                    )
                )
        footer = block(100, 2050, 1600, 2110, content="FOOTER")

        blocks = [title] + [b for _, b in cols] + [footer]
        result = order_blocks(blocks, PAGE_W)
        expected = ["TITLE"]
        for c in range(3):
            expected.extend(f"C{c}-P{i}" for i in range(4))
        expected.append("FOOTER")
        assert ids(result) == expected

    def test_spanning_table_between_column_bands(self):
        # Left-top, right-top, full-width table in the middle, left-bottom,
        # right-bottom: the table must sit between the two bands.
        l_top = block(120, 200, 760, 400, content="L-TOP")
        r_top = block(880, 200, 1520, 400, content="R-TOP")
        table = block(100, 600, 1600, 1200, label="table", content="TABLE")
        l_bot = block(120, 1400, 760, 1600, content="L-BOT")
        r_bot = block(880, 1400, 1520, 1600, content="R-BOT")

        result = order_blocks([l_top, r_top, table, l_bot, r_bot], PAGE_W)
        assert ids(result) == ["L-TOP", "R-TOP", "TABLE", "L-BOT", "R-BOT"]

    def test_narrow_centered_heading_does_not_merge_columns(self):
        # A short centered heading sits astride the gutter: it must not count
        # as coverage, or the columns merge into one band. It overlaps both
        # bands, so it acts as a separator at its vertical position.
        width = 1275.0
        blocks = [
            block(283, 154, 987, 190, label="doc_title", content="TITLE"),
            block(595, 216, 677, 235, content="HEADING"),
            block(144, 423, 614, 548, content="LEFT-1"),
            block(654, 423, 1095, 468, content="RIGHT-1"),
            block(144, 570, 590, 669, content="LEFT-2"),
            block(654, 491, 1107, 592, content="RIGHT-2"),
        ]
        result = order_blocks(blocks, width)
        assert ids(result) == [
            "TITLE",
            "HEADING",
            "LEFT-1",
            "LEFT-2",
            "RIGHT-1",
            "RIGHT-2",
        ]

    def test_single_column_keeps_model_order(self):
        # Full-width text blocks: no gutter, so the model's order stands.
        b1 = block(100, 200, 1600, 400, content="B1", order=1)
        b2 = block(100, 450, 1600, 650, content="B2", order=0)
        b3 = block(100, 700, 1600, 900, content="B3", order=2)
        result = order_blocks([b1, b2, b3], PAGE_W)
        assert ids(result) == ["B2", "B1", "B3"]

    def test_single_column_narrow_blocks_keeps_array_order(self):
        # Narrow left-aligned blocks: still one column, no block_order -> array order.
        b1 = block(120, 200, 600, 300, content="B1")
        b2 = block(120, 350, 600, 450, content="B2")
        b3 = block(120, 500, 600, 600, content="B3")
        result = order_blocks([b3, b1, b2], PAGE_W)
        assert ids(result) == ["B3", "B1", "B2"]

    def test_falls_back_when_a_block_lacks_a_bbox(self):
        b1 = block(120, 200, 760, 300, content="B1", order=1)
        b2 = {"block_label": "text", "block_content": "B2", "block_order": 0}  # no bbox
        b3 = block(880, 200, 1520, 300, content="B3", order=2)
        result = order_blocks([b1, b2, b3], PAGE_W)
        # Legacy sort by block_order.
        assert ids(result) == ["B2", "B1", "B3"]

    def test_falls_back_when_page_width_missing(self):
        b1 = block(120, 200, 760, 300, content="B1", order=1)
        b2 = block(880, 200, 1520, 300, content="B2", order=0)
        result = order_blocks([b1, b2], None)
        assert ids(result) == ["B2", "B1"]

    def test_block_ordering_within_column_uses_y_then_model_order(self):
        # Same column: y0 first; ties broken by model block_order.
        a = block(120, 300, 760, 400, content="A", order=0)
        b = block(120, 300, 760, 400, content="B", order=1)  # same y0 as A
        c = block(120, 200, 760, 290, content="C", order=2)
        result = order_blocks([a, b, c], PAGE_W)
        assert ids(result) == ["C", "A", "B"]

    def test_preserves_block_identity_and_length(self):
        blocks = two_column_blocks(z_order=True)
        result = order_blocks(blocks, PAGE_W)
        assert len(result) == len(blocks)
        assert {id(b) for b in result} == {id(b) for b in blocks}
