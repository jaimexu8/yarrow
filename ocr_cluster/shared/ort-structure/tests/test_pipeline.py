"""Hermetic unit tests for ort_structure.pipeline.

Every InferenceSession is a stub (``StubSession``) that returns hand-built
tensors; no model is ever loaded, so the suite runs on CPU in well under a
second. Each assertion encodes the intended behavior (the ONNX input contract
and the parity-verified numerics), not a mirror of the implementation.
"""
import numpy as np
import pytest

from ort_structure import constants as C
from ort_structure.pipeline import (
    cls_inputs,
    det_resize,
    detr_inputs,
    db_postprocess,
    envelope,
    filter_large_image,
    iou,
    is_contained,
    merge_bboxes,
    nms_layout,
    nms_score,
    process_page,
    reading_order,
    run_cells,
    run_layout,
    run_ocr_det,
    run_table_cls,
)


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------

class StubSession:
    """Stand-in for onnxruntime.InferenceSession.

    ``outputs`` is a callable mapping an input name to the session's output
    list, so each test controls exactly what the (stubbed) model returns.
    """

    def __init__(self, outputs):
        self._outputs = outputs
        self.calls = []

    def run(self, output_names, feed_dict):
        self.calls.append(feed_dict)
        name = "x" if "x" in feed_dict else next(iter(feed_dict))
        return self._outputs(name, feed_dict)

    def get_providers(self):
        return ["CPUExecutionProvider"]


def sessions(**overrides):
    """A dict of the five named stub sessions (the shape pipeline functions take)."""
    base = {
        "doclayout_plusL": StubSession(lambda n, f: []),
        "table_cls": StubSession(lambda n, f: []),
        "wired_cell": StubSession(lambda n, f: []),
        "wireless_cell": StubSession(lambda n, f: []),
        "ocrv5_mobile_det": StubSession(lambda n, f: []),
    }
    base.update(overrides)
    return base


def img(h, w, seed=0):
    """Deterministic HWC uint8 image."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def box_row(cls_id, score, x1, y1, x2, y2):
    """One raw DETR/layout output row [cls, score, x1, y1, x2, y2]."""
    return np.array([cls_id, score, x1, y1, x2, y2], dtype=np.float32)


# ---------------------------------------------------------------------------
# detr_inputs: the ONNX input contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "h, w",
    [(100, 200), (200, 100), (800, 800), (600, 1000), (1000, 600), (3, 4)],
)
def test_detr_inputs_contract(h, w):
    a = img(h, w, seed=h * 31 + w)
    target = 800
    x, im_shape, scale_factor = detr_inputs(a, target, w, h)
    # CHW, float32, batch of one, stretched to target x target.
    assert x.shape == (1, 3, target, target)
    assert x.dtype == np.float32
    # 0-1 normalization (CommonNormalize scale=1/255, mean=0, std=1), not 0-255.
    assert float(x.min()) >= 0.0 and float(x.max()) <= 1.0
    # im_shape is [target, target] in (h, w) order.
    assert im_shape.shape == (1, 2) and im_shape.dtype == np.float32
    np.testing.assert_array_equal(im_shape, [[target, target]])
    # scale_factor = [target/orig_h, target/orig_w] in (h, w) order.
    assert scale_factor.shape == (1, 2) and scale_factor.dtype == np.float32
    np.testing.assert_allclose(scale_factor, [[target / h, target / w]],
                               rtol=1e-6, atol=0)
    # The channel plane matches a plain (cubic) resize + /255, i.e. the
    # normalization is applied to the resized image.
    import cv2
    resized = cv2.resize(a, (target, target), interpolation=cv2.INTER_CUBIC)
    np.testing.assert_array_equal(
        x[0], (resized.transpose(2, 0, 1).astype(np.float32) / 255.0))


def test_detr_inputs_270_rotated_portrait_vs_landscape():
    """A portrait page and its 270-degree rotation (now landscape) must carry
    the matching (h, w) scale factors. This is the contract the rotated-page
    parity case exercises."""
    target = 800
    ph, pw = 1000, 600  # portrait
    a_p = img(ph, pw, seed=1)
    # 270-degree (== 90 CCW) rotation: (h, w) -> (w, h).
    a_r = np.rot90(a_p, k=3).copy()
    assert a_r.shape[:2] == (pw, ph)  # now landscape 600 x 1000

    x_p, im_p, sf_p = detr_inputs(a_p, target, pw, ph)
    x_r, im_r, sf_r = detr_inputs(a_r, target, ph, pw)  # w, h swapped for the rotated page

    # Both are 800x800 stretches; only the scale factors differ.
    assert x_p.shape == x_r.shape == (1, 3, target, target)
    np.testing.assert_array_equal(im_p, [[target, target]])
    np.testing.assert_array_equal(im_r, [[target, target]])
    np.testing.assert_allclose(sf_p, [[target / ph, target / pw]], rtol=1e-6)
    np.testing.assert_allclose(sf_r, [[target / pw, target / ph]], rtol=1e-6)
    # The two scale factors are swaps of each other (h<->w): portrait's [1/h, 1/w]
    # equals the rotated landscape's [1/w, 1/h].
    np.testing.assert_allclose(sf_p, sf_r[:, ::-1], rtol=1e-6)


def test_detr_inputs_cell_target_640():
    a = img(300, 200, seed=9)
    x, im_shape, scale_factor = detr_inputs(a, 640, 200, 300)
    assert x.shape == (1, 3, 640, 640)
    np.testing.assert_array_equal(im_shape, [[640, 640]])
    np.testing.assert_allclose(scale_factor, [[640 / 300, 640 / 200]], rtol=1e-6)


# ---------------------------------------------------------------------------
# cls_inputs: 256-resize, 224 center crop, ImageNet norm
# ---------------------------------------------------------------------------

def test_cls_inputs_contract():
    a = img(100, 300, seed=4)  # landscape crop
    x = cls_inputs(a)
    assert x.shape == (1, 3, 224, 224)
    assert x.dtype == np.float32
    # ImageNet normalization is visible: not in [0,1], spans negative values.
    assert float(x.min()) < 0.0
    assert float(x.max()) > 1.0


def test_cls_inputs_portrait_crop():
    a = img(300, 100, seed=5)  # portrait crop
    x = cls_inputs(a)
    assert x.shape == (1, 3, 224, 224)
    assert float(x.min()) < 0.0 and float(x.max()) > 1.0


# ---------------------------------------------------------------------------
# det_resize: 736 limit, 32-round, max-side cap
# ---------------------------------------------------------------------------

def test_det_resize_small_image_scales_short_side_to_736():
    a = img(100, 200, seed=7)
    r, shape = det_resize(a)
    assert r.dtype == np.float32
    # shape vector is [orig_h, orig_w, rh/orig_h, rw/orig_w].
    assert list(shape[:2]) == [100, 200]
    # Short side (100) is scaled up so the short side is ~736.
    assert abs(r.shape[0] - 736) <= 32
    assert abs(r.shape[1] - 1472) <= 32
    # 32-rounding, each dim >= 32.
    assert r.shape[0] % 32 == 0 and r.shape[1] % 32 == 0
    assert r.shape[0] >= 32 and r.shape[1] >= 32
    # Scale factors round-trip the original dims within 1 px.
    assert abs(shape[2] * 100 - r.shape[0]) <= 1
    assert abs(shape[3] * 200 - r.shape[1]) <= 1


def test_det_resize_large_side_capped_at_det_max_side(monkeypatch):
    monkeypatch.setattr(C, "DET_MAX_SIDE", 768)
    a = img(2000, 1000, seed=8)  # short side 1000 > 736, so ratio=1.0
    r, shape = det_resize(a)
    # Without the cap the long side would stay 2000; the cap must shrink it.
    assert max(r.shape) <= C.DET_MAX_SIDE + 32
    assert r.shape[0] % 32 == 0 and r.shape[1] % 32 == 0


def test_det_resize_large_image_keeps_ratio_1():
    a = img(800, 1200, seed=10)  # both sides >= 736
    r, shape = det_resize(a)
    # ratio stays 1.0, so output dims ~= input dims (32-rounded).
    assert abs(r.shape[0] - 800) <= 16
    assert abs(r.shape[1] - 1200) <= 16


# ---------------------------------------------------------------------------
# iou: float math (the int16 regression lives here)
# ---------------------------------------------------------------------------

def test_iou_identical_is_one():
    b = [10, 10, 50, 50]
    assert iou(b, b) == pytest.approx(1.0)


def test_iou_disjoint_is_zero():
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_iou_known_overlap():
    # plus_one=True (default): each box is 31x31 (area 961). Intersection
    # [10,10,30,30] is 21x21=441. IoU = 441 / (961 + 961 - 441).
    assert iou([0, 0, 30, 30], [10, 10, 40, 40]) == pytest.approx(441 / 1481)


def test_iou_without_plus_one():
    # plus_one=False: plain pixel areas, 30x30 boxes (area 900 each).
    # Intersection [10,10,30,30] is 20x20=400.
    assert iou([0, 0, 30, 30], [10, 10, 40, 40], plus_one=False) == pytest.approx(400 / 1400)


def test_iou_plus_one_changes_result():
    # Identical boxes always give IoU 1.0, so use a partial overlap where the
    # +1 convention is visible: 10x10 boxes overlapping by 5 in x.
    # plus_one=True -> 0.375; plus_one=False -> 1/3.
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(0.375)
    assert iou([0, 0, 10, 10], [5, 0, 15, 10], plus_one=False) == pytest.approx(1 / 3)


def test_iou_int16_overflow_regression():
    """The parity bug: det polys and cell boxes arrive as np.int16, and an int16
    scalar product overflows ("Python integer 32820 out of bounds for int16").

    Two boxes near 32767 produce an int16 area of ~1.07e9, which int16 cannot
    hold. The float path must return the correct IoU; a naive int16 implementation
    wraps or raises instead.
    """
    b1 = np.array([0, 0, 32767, 32767], dtype=np.int16)
    b2 = np.array([0, 0, 16384, 32767], dtype=np.int16)
    # Ground truth (float, plus_one=True default): each box's area is
    # (x2 - x1 + 1) * (y2 - y1 + 1); the intersection is box2's whole area.
    inter = 16385 * 32768
    area1 = 32768 * 32768
    area2 = 16385 * 32768
    expected = inter / (area1 + area2 - inter)
    got = iou(b1, b2)
    # A correct float IoU is in [0, 1]; an int16 overflow gives a wrapped or
    # bogus value (or an exception), so both assertions catch it.
    assert 0.0 <= got <= 1.0
    assert got == pytest.approx(expected, rel=1e-6)
    # And the int16 path would NOT produce this: area1 as int16 overflows.
    with pytest.raises((OverflowError, FloatingPointError)):
        _ = np.int16(area1)  # area1 = 1073709189 > int16 max


def test_iou_large_coords_do_not_overflow():
    """Coordinates up to 65535 (beyond int16's 32767 max) still yield a valid
    [0,1] IoU: this locks the bug class in general, not just the 32820 case."""
    b1 = [0, 0, 65535, 65535]
    b2 = [0, 0, 32768, 65535]
    got = iou(b1, b2)
    assert 0.0 <= got <= 1.0
    # plus_one=True (default): area = (x2-x1+1)*(y2-y1+1).
    inter = (32768 - 0 + 1) * (65535 - 0 + 1)
    area1 = (65535 - 0 + 1) * (65535 - 0 + 1)
    area2 = (32768 - 0 + 1) * (65535 - 0 + 1)
    assert got == pytest.approx(inter / (area1 + area2 - inter), rel=1e-6)


# ---------------------------------------------------------------------------
# nms_layout: 0.6 same-class, 0.98 cross-class
# ---------------------------------------------------------------------------

def test_nms_layout_suppresses_same_class_at_0p6():
    # Two same-class boxes overlapping at IoU ~0.5 (below 0.6) -> both kept.
    boxes = np.array([
        [8, 0.9, 0, 0, 100, 100],
        [8, 0.8, 60, 60, 160, 160],
    ], dtype=np.float32)
    kept = nms_layout(boxes)
    assert sorted(kept) == [0, 1]


def test_nms_layout_suppresses_same_class_overlap():
    # Two same-class boxes with IoU > 0.6 -> only the higher-score box survives.
    boxes = np.array([
        [8, 0.9, 0, 0, 100, 100],
        [8, 0.8, 5, 5, 105, 105],   # IoU ~= 0.83 with box 0
    ], dtype=np.float32)
    kept = nms_layout(boxes)
    assert kept == [0]


def test_nms_layout_keeps_disjoint_classes():
    # Different classes are only suppressed at IoU >= 0.98. These two boxes
    # overlap at IoU ~0.90 (below 0.98), so both survive even though a
    # same-class pair at that overlap would be NMS'd at 0.6.
    boxes = np.array([
        [8, 0.9, 0, 0, 100, 100],
        [2, 0.8, 10, 10, 100, 100],
    ], dtype=np.float32)
    kept = nms_layout(boxes)
    assert sorted(kept) == [0, 1]


def test_nms_layout_int16_boxes_use_float_iou():
    """Boxes arriving as int16 (the real DETR output dtype) must be compared
    with float IoU, not int16 products."""
    boxes = np.array([
        [8, 0.9, 0, 0, 32767, 32767],
        [8, 0.8, 32000, 32000, 32767, 32767],
    ], dtype=np.int16)
    # IoU of the corner box against the big one is small -> both kept.
    kept = nms_layout(boxes)
    assert sorted(kept) == [0, 1]


def test_nms_layout_empty():
    assert nms_layout(np.zeros((0, 6), dtype=np.float32)) == []


# ---------------------------------------------------------------------------
# merge_bboxes: plus-L "large" mode
# ---------------------------------------------------------------------------

def test_merge_bboxes_large_drops_contained_same_class():
    # paragraph_title (cls 0, "large" mode): a small title inside a big title
    # is dropped; the big one is kept.
    boxes = np.array([
        [0, 0.9, 0, 0, 100, 100],     # big
        [0, 0.8, 20, 20, 80, 80],     # contained in the big one
    ], dtype=np.float32)
    out = merge_bboxes(boxes)
    assert len(out) == 1
    assert out[0][0] == 0
    # The surviving box is the outer (larger) one.
    assert float(out[0][2]) == 0.0 and float(out[0][4]) == 100.0


def test_merge_bboxes_union_mode_keeps_both():
    # text (cls 2, "union" mode) is skipped by the merge entirely.
    boxes = np.array([
        [2, 0.9, 0, 0, 100, 100],
        [2, 0.8, 20, 20, 80, 80],
    ], dtype=np.float32)
    out = merge_bboxes(boxes)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# filter_large_image: 0.82 landscape / 0.93 portrait
# ---------------------------------------------------------------------------

def test_filter_large_image_drops_full_page_image_box():
    W, H = 1000, 800  # landscape, threshold 0.82
    boxes = np.array([
        [1, 0.9, 0, 0, 1000, 800],        # image, 100% coverage -> dropped
        [1, 0.9, 0, 0, 700, 500],         # image, 0.44 coverage -> kept
        [2, 0.9, 0, 0, 1000, 800],        # text, full page      -> kept (not image)
    ], dtype=np.float32)
    out = filter_large_image(boxes, W, H)
    # Exactly the full-page image box is dropped; the small image and the
    # full-page text box survive.
    assert len(out) == 2
    small_image = [b for b in out if b[0] == 1 and float(b[4]) == 700.0]
    full_text = [b for b in out if b[0] == 2]
    assert len(small_image) == 1
    assert len(full_text) == 1


def test_filter_large_image_keeps_image_box_under_threshold():
    W, H = 1000, 800
    # 0.80 coverage < 0.82 -> kept.
    boxes = np.array([[1, 0.9, 0, 0, 800, 800]], dtype=np.float32)
    out = filter_large_image(boxes, W, H)
    assert len(out) == 1


def test_filter_large_image_portrait_threshold_is_higher():
    # Portrait page (W<=H): the drop threshold is 0.93, higher than the 0.82
    # used for landscape. A box covering 0.90 of a portrait page is kept, while
    # the same 0.90 coverage on a landscape page is dropped.
    W, H = 800, 1000  # portrait, area 800000
    portrait_box = np.array([[1, 0.9, 0, 0, 720, 1000]], dtype=np.float32)  # 0.90 coverage
    assert len(filter_large_image(portrait_box, W, H)) == 1  # kept (0.90 < 0.93)

    Wl, Hl = 1000, 800  # landscape, area 800000
    landscape_box = np.array([[1, 0.9, 0, 0, 900, 800]], dtype=np.float32)  # 0.90 coverage
    assert filter_large_image(landscape_box, Wl, Hl) == []  # dropped (0.90 > 0.82)


def test_filter_large_image_landscape_0p82_band():
    # Pin the landscape 0.82 threshold: 0.80 kept, 0.85 dropped.
    W, H = 1000, 800
    kept = np.array([[1, 0.9, 0, 0, 800, 800]], dtype=np.float32)     # 0.80 coverage
    dropped = np.array([[1, 0.9, 0, 0, 850, 800]], dtype=np.float32)  # 0.85 coverage
    assert len(filter_large_image(kept, W, H)) == 1
    assert filter_large_image(dropped, W, H) == []


def test_filter_large_image_never_drops_non_image_boxes():
    W, H = 1000, 800
    boxes = np.array([
        [2, 0.9, 0, 0, 1000, 800],
        [10, 0.9, 0, 0, 1000, 800],
    ], dtype=np.float32)
    out = filter_large_image(boxes, W, H)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# is_contained
# ---------------------------------------------------------------------------

def test_is_contained_threshold_0p9():
    big = [0, 0, 0, 0, 100, 100]
    inner_95 = [0, 0, 0, 0, 95, 95]        # fully inside, 90% of its area -> contained
    partly_out = [0, 0, 50, 50, 150, 150]  # sticks out; only 25% of its area inside
    assert is_contained(inner_95, big) is True
    assert is_contained(partly_out, big) is False


def test_is_contained_empty_box():
    # A zero-area box is never "contained" (returns False, no division error).
    assert is_contained([0, 0, 10, 10, 10, 10], [0, 0, 0, 0, 100, 100]) is False


# ---------------------------------------------------------------------------
# reading_order
# ---------------------------------------------------------------------------

def _box(label, x, y, w=50, h=20):
    return {"label": label, "score": 0.9,
            "coordinate": [x, y, x + w, y + h]}


def test_reading_order_single_column_top_to_bottom():
    boxes = [
        _box("text", 50, 300),
        _box("text", 50, 100),
        _box("text", 50, 200),
    ]
    assert reading_order(boxes) == [1, 2, 0]


def test_reading_order_two_column_is_xycut():
    # xycut sorts by top first: the top row (both columns) comes before the
    # bottom row, and within a row by left.
    boxes = [
        _box("text", 600, 100),   # 0: right, top
        _box("text", 50, 100),    # 1: left,  top
        _box("text", 50, 300),    # 2: left,  bottom
        _box("text", 600, 300),   # 3: right, bottom
    ]
    assert reading_order(boxes) == [1, 0, 2, 3]


def test_reading_order_ignores_markdown_labels_for_counter():
    # The block_order counter only advances for labels in VISUALIZE_ORDER_LABELS.
    # "seal" is not in that set, so it must not consume a counter value. This is
    # verified at the process_page level below; here we confirm the label set.
    assert "seal" not in C.VISUALIZE_ORDER_LABELS
    assert "text" in C.VISUALIZE_ORDER_LABELS
    assert "paragraph_title" in C.VISUALIZE_ORDER_LABELS


# ---------------------------------------------------------------------------
# run_layout: full stub-driven path
# ---------------------------------------------------------------------------

def test_run_layout_filters_nms_and_clamps():
    # One box below the text threshold (dropped), one overlapping the kept box
    # (NMS'd), one out of bounds (clamped). The kept box has the highest score so
    # it is deterministically the first NMS survivor.
    sess = StubSession(lambda n, f: (
        np.array([
            box_row(2, 0.2, 0, 0, 100, 100),        # score < 0.4 (text thr) -> dropped
            box_row(2, 0.95, 0, 0, 100, 100),       # highest score -> kept
            box_row(2, 0.9, 5, 5, 105, 105),        # overlaps kept -> NMS'd
            box_row(2, 0.8, 900, -50, 2000, 1200),  # out of bounds -> clamped
        ], dtype=np.float32),
        np.array([4], dtype=np.int64),
    ))
    boxes = run_layout(sessions(doclayout_plusL=sess), img(800, 1000, seed=11))
    labels = [b["label"] for b in boxes]
    coords = [b["coordinate"] for b in boxes]
    assert labels.count("text") == 2
    # The clamped box is bounded by the page (1000 x 800).
    for x1, y1, x2, y2 in coords:
        assert 0.0 <= x1 < x2 <= 1000.0
        assert 0.0 <= y1 < y2 <= 800.0
    # The kept box is the unsuppressed (0,0,100,100); the clamped one fills the
    # bottom-right to the page edge.
    assert [0.0, 0.0, 100.0, 100.0] in coords
    assert [900.0, 0.0, 1000.0, 800.0] in coords


def test_run_layout_empty_when_no_detections():
    sess = StubSession(lambda n, f: (np.zeros((0, 6), dtype=np.float32),
                                     np.array([0], dtype=np.int64)))
    assert run_layout(sessions(doclayout_plusL=sess), img(100, 100, seed=12)) == []


def test_run_layout_counts_truncate_raw_rows():
    # counts says 2, but 3 rows are present; only the first 2 are considered.
    sess = StubSession(lambda n, f: (
        np.array([
            box_row(2, 0.9, 0, 0, 100, 100),
            box_row(2, 0.9, 200, 0, 300, 100),
            box_row(2, 0.9, 400, 0, 500, 100),
        ], dtype=np.float32),
        np.array([2], dtype=np.int64),
    ))
    boxes = run_layout(sessions(doclayout_plusL=sess), img(400, 600, seed=13))
    assert len(boxes) == 2


# ---------------------------------------------------------------------------
# run_table_cls / run_cells
# ---------------------------------------------------------------------------

def test_run_table_cls_picks_argmax():
    sess = StubSession(lambda n, f: [np.array([[0.9, 0.1]])])
    cls, scores = run_table_cls(sess, img(100, 200, seed=14))
    assert cls == "wired_table"
    np.testing.assert_array_equal(scores, [0.9, 0.1])


def test_run_table_cls_wireless():
    sess = StubSession(lambda n, f: [np.array([[0.1, 0.9]])])
    cls, _ = run_table_cls(sess, img(100, 200, seed=15))
    assert cls == "wireless_table"


def test_run_cells_clamps_to_crop_and_filters_low_score():
    # (0,0,100,100) at score 0.9 is the kept box. (10,10,110,110) at score 0.8
    # overlaps it at IoU ~0.66 > 0.3, so it is NMS'd. A third box at score 0.2
    # is below the 0.3 cell threshold.
    sess = StubSession(lambda n, f: (
        np.array([
            box_row(0, 0.2, 150, 150, 190, 190),  # below 0.3 -> dropped
            box_row(0, 0.9, 0, 0, 100, 100),       # kept
            box_row(0, 0.8, 10, 10, 110, 110),     # IoU ~0.66 > 0.3 -> NMS'd
        ], dtype=np.float32),
        np.array([3], dtype=np.int64),
    ))
    crop = img(200, 200, seed=16)
    cells = run_cells(sessions(wired_cell=sess), crop, "wired_table")
    assert cells == [[0.0, 0.0, 100.0, 100.0]]


def test_run_cells_uses_wired_vs_wireless_model():
    seen = {}

    def mk(name):
        def out(n, f):
            seen[name] = True
            return (np.zeros((0, 6), dtype=np.float32),
                    np.array([0], dtype=np.int64))
        return StubSession(out)

    sess = sessions(wired_cell=mk("wired"), wireless_cell=mk("wireless"))
    crop = img(100, 100, seed=17)
    run_cells(sess, crop, "wired_table")
    assert seen.get("wired") is True and not seen.get("wireless")
    seen.clear()
    run_cells(sess, crop, "wireless_table")
    assert seen.get("wireless") is True and not seen.get("wired")


# ---------------------------------------------------------------------------
# run_ocr_det / db_postprocess: int16 polys, in-bounds
# ---------------------------------------------------------------------------

def _det_prob(shape, value=0.9):
    """A DB prob map: a solid block of high probability in the top-left, low
    elsewhere, so exactly one contour is found. The block is near-square
    (aspect ratio < 2:1) so that unclip cannot self-intersect the quad."""
    prob = np.full(shape, 0.05, dtype=np.float32)
    prob[10:60, 10:80] = value  # 50 x 70, aspect ~1.4
    return prob


def test_db_postprocess_emits_int16_polys_in_bounds():
    prob = _det_prob((100, 200))
    polys, scores = db_postprocess(prob, dest_w=1000, dest_h=800)
    assert len(polys) == 1
    p = polys[0]
    # The known int16 behavior: det polys are np.int16 (4x2).
    assert p.dtype == np.int16
    assert p.shape == (4, 2)
    # Every vertex is within the destination page.
    assert int(p[:, 0].min()) >= 0 and int(p[:, 0].max()) <= 1000
    assert int(p[:, 1].min()) >= 0 and int(p[:, 1].max()) <= 800
    assert 0.0 <= scores[0] <= 1.0


def test_db_postprocess_no_text_is_empty():
    prob = np.full((100, 200), 0.05, dtype=np.float32)
    polys, scores = db_postprocess(prob, dest_w=1000, dest_h=800)
    assert polys == [] and scores == []


def test_run_ocr_det_round_trips_through_det_resize():
    sess = StubSession(lambda n, f: [np.full((1, 1, 100, 200), 0.05, np.float32)])
    # No high-prob region -> no polys; just confirm the feed used det_resize.
    polys, scores = run_ocr_det(sess, img(100, 200, seed=18))
    assert polys == [] and scores == []
    assert len(sess.calls) == 1
    assert "x" in sess.calls[0]


# ---------------------------------------------------------------------------
# process_page / envelope: the worker's envelope contract
# ---------------------------------------------------------------------------

def _full_stub_sessions(W=400, H=300):
    layout = (
        np.array([
            box_row(2, 0.9, 10, 10, 100, 40),    # text
            box_row(8, 0.9, 200, 10, 380, 100),  # table
            box_row(15, 0.9, 10, 200, 100, 260), # seal (not in order set)
        ], dtype=np.float32),
        np.array([3], dtype=np.int64),
    )
    det = np.full((1, 1, 32, 32), 0.05, np.float32)  # no text detected
    cells = (np.zeros((0, 6), dtype=np.float32), np.array([0], dtype=np.int64))
    return sessions(
        doclayout_plusL=StubSession(lambda n, f: layout),
        table_cls=StubSession(lambda n, f: [np.array([[0.9, 0.1]])]),
        wired_cell=StubSession(lambda n, f: cells),
        wireless_cell=StubSession(lambda n, f: cells),
        ocrv5_mobile_det=StubSession(lambda n, f: [det]),
    )


def test_envelope_shape_matches_worker():
    pruned, _ = process_page(_full_stub_sessions(), img(300, 400, seed=19))
    env = envelope(pruned)
    assert set(env) == {"result"}
    lps = env["result"]["layoutParsingResults"]
    assert len(lps) == 1
    pruned_result = lps[0]["prunedResult"]
    # The worker reads these keys.
    for key in ("width", "height", "model_settings", "parsing_res_list",
                "layout_det_res", "overall_ocr_res", "table_res_list"):
        assert key in pruned_result
    # No underscore-prefixed internal keys leak into the envelope.
    assert not any(k.startswith("_") for k in pruned_result)
    assert pruned_result["width"] == 400 and pruned_result["height"] == 300


def test_process_page_reading_order_and_block_order():
    pruned, _ = process_page(_full_stub_sessions(), img(300, 400, seed=19))
    blocks = pruned["parsing_res_list"]
    # xycut reading order: the top-row blocks (text@y10, table@y10) come before
    # the seal@y200; within the top row, left (text x10) before right (table x200).
    labels = [b["block_label"] for b in blocks]
    assert labels == ["text", "table", "seal"]
    # block_order counter advances only for order-set labels: text=0, table=None,
    # seal=None.
    orders = [b["block_order"] for b in blocks]
    assert orders == [0, None, None]
    # block_id is the positional index.
    assert [b["block_id"] for b in blocks] == [0, 1, 2]
    # Every block_bbox is within [0, W] x [0, H].
    for b in blocks:
        x1, y1, x2, y2 = b["block_bbox"]
        assert 0 <= x1 < x2 <= 400
        assert 0 <= y1 < y2 <= 300


def test_process_page_table_cells_shifted_to_page_space():
    # The table crop is at (200,10)-(380,100); a cell at crop-space (0,0,50,50)
    # must be reported in page space (200,10,250,60).
    cells = (
        np.array([box_row(0, 0.9, 0, 0, 50, 50)], dtype=np.float32),
        np.array([1], dtype=np.int64),
    )
    det = np.full((1, 1, 32, 32), 0.05, np.float32)
    sess = sessions(
        doclayout_plusL=StubSession(lambda n, f: (
            np.array([box_row(8, 0.9, 200, 10, 380, 100)], dtype=np.float32),
            np.array([1], dtype=np.int64),
        )),
        table_cls=StubSession(lambda n, f: [np.array([[0.9, 0.1]])]),
        wired_cell=StubSession(lambda n, f: cells),
        wireless_cell=StubSession(lambda n, f: cells),
        ocrv5_mobile_det=StubSession(lambda n, f: [det]),
    )
    pruned, _ = process_page(sess, img(300, 400, seed=20))
    tables = pruned["table_res_list"]
    assert len(tables) == 1
    assert tables[0]["cell_box_list"] == [[200.0, 10.0, 250.0, 60.0]]
    assert tables[0]["pred_html"] == ""


def test_process_page_all_coordinates_in_bounds():
    W, H = 1000, 800
    pruned, _ = process_page(_full_stub_sessions(W, H), img(H, W, seed=21))
    for b in pruned["parsing_res_list"]:
        x1, y1, x2, y2 = b["block_bbox"]
        assert 0 <= x1 <= W and 0 <= y1 <= H and 0 <= x2 <= W and 0 <= y2 <= H
    for b in pruned["layout_det_res"]["boxes"]:
        x1, y1, x2, y2 = b["coordinate"]
        assert 0.0 <= x1 <= W and 0.0 <= y1 <= H and 0.0 <= x2 <= W and 0.0 <= y2 <= H


def test_process_page_returns_elapsed():
    pruned, elapsed = process_page(_full_stub_sessions(), img(300, 400, seed=22))
    assert isinstance(elapsed, float) and elapsed >= 0.0


# ---------------------------------------------------------------------------
# constants: label lists and the 5-model pin
# ---------------------------------------------------------------------------

def test_labels_plusL_twenty_class_mapping():
    assert len(C.LABELS) == 20
    assert C.LABELS[0] == "paragraph_title"
    assert C.LABELS[1] == "image"
    assert C.LABELS[8] == "table"
    assert C.IMAGE_INDEX == 1
    # THRESHOLDS and MERGE_MODE cover all 20 classes.
    assert set(C.THRESHOLDS) == set(range(20))
    assert set(C.MERGE_MODE) == set(range(20))
    assert C.MERGE_MODE[0] == "large" and C.MERGE_MODE[8] == "union"
    assert C.LAYOUT_NMS_IOU_SAME == 0.6
    assert C.LAYOUT_NMS_IOU_DIFF == 0.98


def test_only_five_models_are_loaded():
    # The five pinned models, in load order. V2/V3 layout, rec, textline-ori and
    # the SLANeXt/SLANet table-recognition models are NOT loaded.
    assert C.MODEL_NAMES == [
        "doclayout_plusL", "table_cls", "wired_cell", "wireless_cell",
        "ocrv5_mobile_det",
    ]
    for name in ("doclayout_v2", "doclayout_v3", "ocrv5_mobile_rec",
                 "textline_ori", "slanexw_wired", "slanet_plus"):
        assert name not in C.MODEL_NAMES
    # MEM_CAPS covers exactly the five loaded models.
    assert set(C.MEM_CAPS) == set(C.MODEL_NAMES)
