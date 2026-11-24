"""Property-based tests for the pure ort_structure.pipeline functions.

Each property encodes a real contract of the function, not a mirror of the implementation.
If a candidate invariant does not hold, it is either a bug (fix it) or not a
contract (do not assert it).

Invariants asserted:
  * detr_inputs: for any HWC uint8 image, output is CHW float32 (1,3,T,T),
    values in [0,1], im_shape == [[T,T]], scale_factor == [[T/h, T/w]].
  * det_resize: output dims are multiples of 32 (each >= 32), the returned
    shape vector [h, w, rh/h, rw/w] round-trips to the original dims within 1
    px, and the long side never exceeds DET_MAX_SIDE (within one 32-px bucket).
  * iou: for any two valid boxes, iou in [0,1]; iou(b,b) == 1.0; strictly
    separated boxes give 0.0; and the int16-overflow regression as a property:
    boxes stored as np.int16 (the real ONNX output dtype) whose area overflows
    int16 must still yield a float-correct IoU, and boxes with coordinates up
    to 65535 (beyond int16's 32767 max) must yield a valid [0,1] value.
  * nms_score: output is a subset of the input (no new boxes), scores
    descending, and no two survivors overlap above the threshold.
  * filter_large_image: a filter never drops a non-image-labeled box.
  * process_page: every emitted block_bbox / layout coordinate is within
    [0,W] x [0,H] (the plan's "coordinates 100% in-bounds" claim), driven by a
    stubbed session.
"""
import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ort_structure import constants as C
from ort_structure.pipeline import (
    det_resize,
    detr_inputs,
    envelope,
    filter_large_image,
    iou,
    nms_score,
    process_page,
)


PROFILE = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# strategies (built to avoid heavy assume() filtering)
# ---------------------------------------------------------------------------

def image(h=st.integers(1, 300), w=st.integers(1, 300)):
    """HWC uint8 image with a fixed shape per draw."""
    return st.builds(
        lambda h, w, seed: (
            h, w,
            np.random.default_rng(seed).integers(
                0, 256, size=(h, w, 3), dtype=np.uint8),
        ),
        h=h, w=w, seed=st.integers(0, 2**31 - 1),
    )


def valid_box(max_coord=3000):
    """A valid [x1, y1, x2, y2] box with x2>x1 and y2>y1, built directly
    (x2 = x1 + width) so no filtering is needed."""
    return st.tuples(
        st.integers(0, max_coord), st.integers(0, max_coord),
        st.integers(1, max_coord), st.integers(1, max_coord),
    ).map(lambda t: (t[0], t[1], t[0] + t[2], t[1] + t[3]))


@st.composite
def box_score_pairs(draw, n=st.integers(0, 12), max_coord=2000):
    """Boxes and scores of the SAME length, built together (no assume)."""
    n = draw(n)
    boxes = [draw(valid_box(max_coord)) for _ in range(n)]
    scores = [
        draw(st.floats(0.0, 1.0, allow_nan=False)) for _ in range(n)
    ]
    return boxes, scores


@st.composite
def int16_box(draw):
    """A valid box whose four coordinates all fit in np.int16 (0..32767), but
    whose area (width*height, up to ~32767^2) overflows int16. This is the real
    ONNX situation: the coordinates are int16, the area is not. Includes large
    boxes so the int16-area overflow is actually exercised."""
    x1 = draw(st.integers(0, 32766))
    x2 = draw(st.integers(x1 + 1, 32767))
    y1 = draw(st.integers(0, 32766))
    y2 = draw(st.integers(y1 + 1, 32767))
    return (x1, y1, x2, y2)


@st.composite
def disjoint_box_pair(draw):
    """Two boxes separated by a gap of >= 1 px in x, so even with the
    plus_one convention they do not overlap (IoU 0). Built directly."""
    x1 = draw(st.integers(0, 1000)); y1 = draw(st.integers(0, 1000))
    w1 = draw(st.integers(1, 100)); h1 = draw(st.integers(1, 100))
    b1 = (x1, y1, x1 + w1, y1 + h1)
    gap = draw(st.integers(1, 500))
    x2 = x1 + w1 + gap
    y2 = y1 + draw(st.integers(0, 500))
    w2 = draw(st.integers(1, 100)); h2 = draw(st.integers(1, 100))
    return b1, (x2, y2, x2 + w2, y2 + h2)


# ---------------------------------------------------------------------------
# detr_inputs
# ---------------------------------------------------------------------------

@given(spec=image(), target=st.sampled_from([640, 800]))
@PROFILE
def test_detr_inputs_contract(spec, target):
    h, w, img = spec
    x, im_shape, scale_factor = detr_inputs(img, target, w, h)
    assert x.shape == (1, 3, target, target)
    assert x.dtype == np.float32
    # 0-1 normalization, not raw 0-255.
    assert float(x.min()) >= 0.0 and float(x.max()) <= 1.0
    np.testing.assert_array_equal(im_shape, [[target, target]])
    # scale_factor == [[target/h, target/w]] (h, w order).
    np.testing.assert_allclose(scale_factor, [[target / h, target / w]], rtol=1e-6)


# ---------------------------------------------------------------------------
# det_resize
# ---------------------------------------------------------------------------

@given(spec=image())
@PROFILE
def test_det_resize_dims_are_32_multiples_and_roundtrip(spec):
    h, w, img = spec
    r, shape = det_resize(img)
    rh, rw = r.shape[:2]
    assert rh % 32 == 0 and rw % 32 == 0
    assert rh >= 32 and rw >= 32
    assert list(shape[:2]) == [h, w]
    # The scale factors round-trip back to the output dims within 1 px.
    assert abs(shape[2] * h - rh) <= 1
    assert abs(shape[3] * w - rw) <= 1


@given(spec=image(h=st.integers(1, 5000), w=st.integers(1, 5000)))
@PROFILE
def test_det_resize_never_exceeds_max_side(spec):
    h, w, img = spec
    r, _ = det_resize(img)
    # The cap is applied before the final 32-round, so the long side is within
    # one 32-px bucket of DET_MAX_SIDE.
    assert max(r.shape) <= C.DET_MAX_SIDE + 32


# ---------------------------------------------------------------------------
# iou
# ---------------------------------------------------------------------------

def ref_iou_float(b1, b2, plus_one=True):
    """Reference IoU in pure float (plus_one convention), computed from ints so
    the reference itself cannot overflow."""
    p = 1 if plus_one else 0
    a1 = max(int(b1[0]), int(b2[0]))
    b1t = max(int(b1[1]), int(b2[1]))
    a2 = min(int(b1[2]), int(b2[2]))
    b2t = min(int(b1[3]), int(b2[3]))
    w = max(0.0, a2 - a1 + p)
    h = max(0.0, b2t - b1t + p)
    inter = w * h
    area1 = (int(b1[2]) - int(b1[0]) + p) * (int(b1[3]) - int(b1[1]) + p)
    area2 = (int(b2[2]) - int(b2[0]) + p) * (int(b2[3]) - int(b2[1]) + p)
    denom = area1 + area2 - inter
    return inter / denom if denom > 0 else 0.0


@given(b1=valid_box(), b2=valid_box())
@PROFILE
def test_iou_in_unit_interval(b1, b2):
    assert 0.0 <= iou(b1, b2) <= 1.0


@given(b=valid_box())
@PROFILE
def test_iou_self_is_one(b):
    assert iou(b, b) == 1.0


@given(pair=disjoint_box_pair())
@PROFILE
def test_iou_separated_boxes_is_zero(pair):
    b1, b2 = pair
    assert iou(b1, b2) == 0.0


@given(b1=int16_box(), b2=int16_box())
@PROFILE
def test_iou_int16_area_overflow_matches_float_reference(b1, b2):
    """The parity regression, as a property. The real det polys / cell boxes
    arrive as np.int16; their AREA (width*height, up to ~32767^2) overflows
    int16 ("Python integer 32820 out of bounds for int16" class). The float
    path must match a float reference. If iou did int16 area math, large areas
    would wrap and this would fail."""
    a1 = np.array(b1, dtype=np.int16)
    a2 = np.array(b2, dtype=np.int16)
    got = iou(a1, a2)
    ref = ref_iou_float(b1, b2)
    # Tight tolerance: int16 overflow is off by orders of magnitude, so a
    # ~1e-9 window still catches it while allowing float64 rounding noise.
    assert got == pytest.approx(ref, rel=1e-9, abs=1e-9)


@given(b1=valid_box(max_coord=65535), b2=valid_box(max_coord=65535))
@PROFILE
def test_iou_large_coords_up_to_65535_in_unit_interval(b1, b2):
    # Coordinates beyond int16's 32767 max (up to 65535) must still yield a
    # valid [0,1] IoU. Locks the bug class in general, not just one case.
    assert 0.0 <= iou(b1, b2) <= 1.0


# ---------------------------------------------------------------------------
# nms_score
# ---------------------------------------------------------------------------

@given(pairs=box_score_pairs())
@PROFILE
def test_nms_score_is_a_subset_with_descending_scores(pairs):
    boxes, scores = pairs
    kept_boxes, kept_scores = nms_score(boxes, scores, 0.3)
    # No new boxes: every kept box is one of the input boxes (nms_score
    # upcasts to float64 and returns .tolist(), so compare as float lists).
    input_as_lists = [list(b) for b in boxes]
    for kb in kept_boxes:
        assert list(kb) in input_as_lists
    # Kept scores are a subset of the input scores and descending.
    for ks in kept_scores:
        assert ks in scores
    for i in range(len(kept_scores) - 1):
        assert kept_scores[i] >= kept_scores[i + 1]


@given(pairs=box_score_pairs(n=st.integers(2, 8), max_coord=1000))
@PROFILE
def test_nms_score_survivors_do_not_exceed_threshold(pairs):
    boxes, scores = pairs
    thr = 0.3
    kept_boxes, _ = nms_score(boxes, scores, thr)
    # nms_score uses the plain (plus_one=False) IoU. Because each survivor was
    # checked against every earlier-picked survivor at pick time, no two
    # survivors overlap above the threshold.
    for i in range(len(kept_boxes)):
        for j in range(i + 1, len(kept_boxes)):
            a, b = kept_boxes[i], kept_boxes[j]
            ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = ix * iy
            area_a = (a[2] - a[0]) * (a[3] - a[1])
            area_b = (b[2] - b[0]) * (b[3] - b[1])
            denom = area_a + area_b - inter
            if denom > 0:
                assert inter / denom <= thr + 1e-9


# ---------------------------------------------------------------------------
# filter_large_image
# ---------------------------------------------------------------------------

@given(
    boxes=st.lists(
        st.tuples(
            st.integers(0, 19),  # class id
            st.floats(0.0, 1.0, allow_nan=False),  # score
            st.integers(0, 1000), st.integers(0, 1000),
            st.integers(0, 1000), st.integers(0, 1000),
        ),
        min_size=0, max_size=20,
    ),
    W=st.integers(100, 2000), H=st.integers(100, 2000),
)
@PROFILE
def test_filter_large_image_never_drops_non_image_boxes(boxes, W, H):
    arr = [list(b) for b in boxes]
    out = filter_large_image(np.array(arr, dtype=np.float32), W, H)
    # A filter must never drop a non-image-labeled box.
    non_image_in = [b for b in arr if b[0] != C.IMAGE_INDEX]
    non_image_out = [b for b in out if b[0] != C.IMAGE_INDEX]
    assert len(non_image_out) == len(non_image_in)


# ---------------------------------------------------------------------------
# process_page / envelope: coordinates in-bounds (the "100% in-bounds" claim)
# ---------------------------------------------------------------------------

def _stub_sessions(layout_rows):
    """Stub sessions: layout returns the given rows, OCR det returns no text,
    table cls -> wired, cells -> none."""
    import test_pipeline as tp  # sibling test module (tests dir on sys.path)
    StubSession = tp.StubSession
    layout = (
        np.array(layout_rows, dtype=np.float32) if layout_rows
        else np.zeros((0, 6), dtype=np.float32),
        np.array([len(layout_rows)], dtype=np.int64),
    )
    det = np.full((1, 1, 32, 32), 0.05, np.float32)
    cells = (np.zeros((0, 6), dtype=np.float32), np.array([0], dtype=np.int64))
    return {
        "doclayout_plusL": StubSession(lambda n, f: layout),
        "table_cls": StubSession(lambda n, f: [np.array([[0.9, 0.1]])]),
        "wired_cell": StubSession(lambda n, f: cells),
        "wireless_cell": StubSession(lambda n, f: cells),
        "ocrv5_mobile_det": StubSession(lambda n, f: [det]),
    }


def _layout_box(label, x1, y1, x2, y2, score=0.9):
    return [C.LABELS.index(label), score, x1, y1, x2, y2]


@given(
    W=st.integers(100, 2000),
    H=st.integers(100, 2000),
    rows=st.lists(
        st.tuples(
            st.sampled_from(["text", "paragraph_title", "table", "seal", "image"]),
            st.integers(-100, 2200),  # x1 (may be out of bounds)
            st.integers(-100, 2200),  # y1
            st.integers(-100, 2200),  # x2
            st.integers(-100, 2200),  # y2
        ),
        min_size=0, max_size=6,
    ),
)
@PROFILE
def test_process_page_coordinates_always_in_bounds(W, H, rows):
    import test_pipeline as tp  # sibling test module (tests dir on sys.path)
    # Only feed well-formed boxes (x2>x1, y2>y1); the in-bounds contract is
    # about clamping out-of-range boxes, not about rejecting degenerate ones.
    layout_rows = [
        _layout_box(label, x1, y1, x2, y2)
        for label, x1, y1, x2, y2 in rows
        if x2 > x1 and y2 > y1
    ]
    pruned, _ = process_page(_stub_sessions(layout_rows), tp.img(H, W, seed=1))
    pruned_result = envelope(pruned)["result"]["layoutParsingResults"][0]["prunedResult"]
    # Every emitted block_bbox is within [0, W] x [0, H].
    for b in pruned_result["parsing_res_list"]:
        x1, y1, x2, y2 = b["block_bbox"]
        assert 0 <= x1 <= W and 0 <= y1 <= H
        assert 0 <= x2 <= W and 0 <= y2 <= H
    # Every layout box is within [0, W] x [0, H].
    for b in pruned_result["layout_det_res"]["boxes"]:
        x1, y1, x2, y2 = b["coordinate"]
        assert 0.0 <= x1 <= W and 0.0 <= y1 <= H
        assert 0.0 <= x2 <= W and 0.0 <= y2 <= H
    # Dimension consistency.
    assert pruned_result["width"] == W and pruned_result["height"] == H
