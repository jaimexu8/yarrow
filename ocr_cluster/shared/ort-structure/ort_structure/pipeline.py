"""Pure, framework-free port of the parity-verified ORT structure pipeline.

Every pipeline function here is a plain function of (InferenceSession(s),
numpy image) -> plain data. There is no FastAPI, no I/O and no global mutable
state in this module: model loading and session construction live in the thin
:func:`load_sessions` / :class:`Pipeline` boundary at the bottom. Tests stub
``InferenceSession`` and never load a model.

Known behavior preserved on purpose:
* OCR-det polygons and table-cell boxes are emitted as ``np.int16``
  (PaddleX-compatible), so downstream consumers must upcast to float before
  any area math: an int16 scalar product overflows well before 32767*32767.
* IoU/NMS math upcasts box coordinates to float (the original already does
  this in ``nms_score``; the layout IoU takes plain Python numbers).
"""
from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import onnxruntime as ort

from . import constants as C


# ---------------------------------------------------------------------------
# input construction (the ONNX input contract)
# ---------------------------------------------------------------------------

def detr_inputs(img_hwc: np.ndarray, target: int,
                orig_w: int, orig_h: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stretch (keep_ratio=False) to target x target, normalize to 0-1, CHW.

    im_shape=[target, target]; scale_factor=[target/orig_h, target/orig_w]
    (h, w order, matching PaddleX ToBatch). The ONNX graph bakes in the DETR
    postprocess, so returned boxes are already in ORIGINAL pixel space.

    The RT-DETR layout/cell ONNX models expect 0-1 normalized input (PaddleX
    CommonNormalize with mean=0, std=1, scale=1/255), NOT raw 0-255.
    """
    resized = cv2.resize(img_hwc, (target, target), interpolation=cv2.INTER_CUBIC)
    chw = np.ascontiguousarray(
        (resized.transpose(2, 0, 1).astype(np.float32) / 255.0))
    im_shape = np.array([[target, target]], dtype=np.float32)
    scale_factor = np.array(
        [[target / orig_h, target / orig_w]], dtype=np.float32)
    return chw[None], im_shape, scale_factor


def cls_inputs(crop_hwc: np.ndarray) -> np.ndarray:
    """Resize short side to 256, center-crop 224x224, ImageNet norm, CHW."""
    h, w = crop_hwc.shape[:2]
    if h <= w:
        nh, nw = 256, max(1, int(round(w * 256 / h)))
    else:
        nw, nh = 256, max(1, int(round(h * 256 / w)))
    r = cv2.resize(crop_hwc, (nw, nh), interpolation=cv2.INTER_LINEAR)
    ch, cw = r.shape[:2]
    ys = (ch - 224) // 2
    xs = (cw - 224) // 2
    r = r[ys:ys + 224, xs:xs + 224].astype(np.float32)
    r = (r / 255.0 - C.MEAN) / C.STD
    return np.ascontiguousarray(r.transpose(2, 0, 1))[None]


def det_resize(img_hwc: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """DetResizeForTest limit_side_len=736, limit_type=min, 32-round.

    Returns (resized float32 HWC, shape vector [orig_h, orig_w, rh/orig_h,
    rw/orig_w]) exactly as the PaddleX pipeline feeds the DB det model.
    """
    h, w = img_hwc.shape[:2]
    if min(h, w) < C.DET_RESIZE_LIMIT:
        ratio = float(C.DET_RESIZE_LIMIT) / (h if h < w else w)
    else:
        ratio = 1.0
    rh, rw = int(h * ratio), int(w * ratio)
    if max(rh, rw) > C.DET_MAX_SIDE:
        r2 = float(C.DET_MAX_SIDE) / max(rh, rw)
        rh, rw = int(rh * r2), int(rw * r2)
    rh = max(int(round(rh / 32) * 32), 32)
    rw = max(int(round(rw / 32) * 32), 32)
    r = cv2.resize(img_hwc, (rw, rh), interpolation=cv2.INTER_LINEAR)
    return r.astype(np.float32), np.array([h, w, rh / h, rw / w])


# ---------------------------------------------------------------------------
# geometry (all IoU math is float; int16 coordinates must not multiply)
# ---------------------------------------------------------------------------

def iou(box1, box2, plus_one: bool = True) -> float:
    """Axis-aligned IoU of two [x1, y1, x2, y2] boxes, in float arithmetic.

    Box coordinates may be int (or int16) numpy scalars from model output:
    the math upcasts to Python float, because an int16 scalar product
    overflows at ~32767*32767
    """
    a1 = max(float(box1[0]), float(box2[0]))
    b1 = max(float(box1[1]), float(box2[1]))
    a2 = min(float(box1[2]), float(box2[2]))
    b2 = min(float(box1[3]), float(box2[3]))
    p = 1 if plus_one else 0
    w = max(0.0, a2 - a1 + p)
    h = max(0.0, b2 - b1 + p)
    inter = w * h
    area1 = (float(box1[2]) - float(box1[0]) + p) * (float(box1[3]) - float(box1[1]) + p)
    area2 = (float(box2[2]) - float(box2[0]) + p) * (float(box2[3]) - float(box2[1]) + p)
    denom = area1 + area2 - inter
    return inter / denom if denom > 0 else 0.0


def nms_layout(boxes, iou_same: float = C.LAYOUT_NMS_IOU_SAME,
               iou_diff: float = C.LAYOUT_NMS_IOU_DIFF) -> List[int]:
    """PaddleX nms(): boxes [cls, score, x1, y1, x2, y2]; returns kept idx.

    Same-class boxes are suppressed at iou_same (0.6), cross-class at
    iou_diff (0.98). Coordinates upcast to float before the IoU math.
    """
    if len(boxes) == 0:
        return []
    scores = boxes[:, 1]
    order = np.argsort(scores)[::-1]
    selected = []
    while len(order) > 0:
        cur = int(order[0])
        selected.append(cur)
        rest = order[1:]
        keep = []
        for i in rest:
            thr = iou_same if boxes[cur, 0] == boxes[i, 0] else iou_diff
            if iou(boxes[cur, 2:], boxes[i, 2:]) < thr:
                keep.append(i)
        order = np.array(keep)
    return selected


def nms_score(boxes, scores, thr: float = C.CELL_NMS_IOU) -> Tuple[List, List]:
    """PaddleX cells_det_results_nms(): boxes [x1, y1, x2, y2]."""
    if len(boxes) == 0:
        return [], []
    boxes = np.asarray(boxes, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    picked = []
    while order.size > 0:
        i = int(order[0])
        picked.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest]); yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest]); yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        ovr = inter / (areas[i] + areas[rest] - inter)
        keep = rest[np.where(ovr <= thr)[0]]
        order = keep
    return boxes[picked].tolist(), scores[picked].tolist()


def is_contained(box1, box2) -> bool:
    """True if >= 90% of box1's area lies inside box2"""
    _, _, x1, y1, x2, y2 = box1
    _, _, x1p, y1p, x2p, y2p = box2
    a = (float(x2) - float(x1)) * (float(y2) - float(y1))
    iw = max(0.0, float(min(x2, x2p)) - float(max(x1, x1p)))
    ih = max(0.0, float(min(y2, y2p)) - float(max(y1, y1p)))
    inter = iw * ih
    return (inter / a if a > 0 else 0.0) >= 0.9


def filter_large_image(boxes, img_w: int, img_h: int) -> list:
    """Drop 'image' boxes that cover >= area_thres of the page.

    Landscape pages (w > h) drop at 0.82 coverage, portrait at 0.93.
    """
    area_thres = 0.82 if img_w > img_h else 0.93
    img_area = img_w * img_h
    img_idx = C.IMAGE_INDEX
    out = []
    for b in boxes:
        if b[0] == img_idx:
            x1 = max(0, b[2]); y1 = max(0, b[3])
            x2 = min(img_w, b[4]); y2 = min(img_h, b[5])
            if (x2 - x1) * (y2 - y1) <= area_thres * img_area:
                out.append(b)
        else:
            out.append(b)
    return out


def merge_bboxes(boxes) -> np.ndarray:
    """PaddleX layout_merge_bboxes_mode: drop contained boxes per mode."""
    keep = np.ones(len(boxes), dtype=bool)
    for ci, mode in C.MERGE_MODE.items():
        if mode == "union":
            continue
        in_cat = np.array([b[0] == ci for b in boxes])
        for i in range(len(boxes)):
            if not in_cat[i]:
                continue
            if mode == "large":
                for j in range(len(boxes)):
                    if j == i or not in_cat[j]:
                        continue
                    if is_contained(boxes[i], boxes[j]):
                        keep[i] = False
            elif mode == "small":
                has_rel = False
                for j in range(len(boxes)):
                    if j == i or not in_cat[j]:
                        continue
                    if is_contained(boxes[i], boxes[j]) or \
                       is_contained(boxes[j], boxes[i]):
                        has_rel = True
                if not has_rel:
                    keep[i] = False
    return boxes[keep]


# ---------------------------------------------------------------------------
# model runs (each takes sessions, no self)
# ---------------------------------------------------------------------------

def run_layout(sessions: Dict[str, "ort.InferenceSession"],
               img_hwc: np.ndarray) -> List[dict]:
    """Layout detection -> list of boxes in original pixel space."""
    orig_h, orig_w = img_hwc.shape[:2]
    image, im_shape, scale_factor = detr_inputs(img_hwc, 800, orig_w, orig_h)
    boxes_raw, counts = sessions["doclayout_plusL"].run(
        None, {"im_shape": im_shape, "image": image, "scale_factor": scale_factor})
    n = int(counts[0])
    b = boxes_raw[:n]
    if b.size == 0:
        return []
    sel = [i for i in range(len(b)) if float(b[i, 1]) > C.THRESHOLDS.get(int(b[i, 0]), 0.5)
           and float(b[i, 0]) >= 0]
    b = b[sel]
    if b.size == 0:
        return []
    kept = nms_layout(b)
    b = b[kept]
    b = filter_large_image(b, orig_w, orig_h)
    b = np.asarray(b, dtype=np.float32)
    b = merge_bboxes(b)
    out = []
    for r in b:
        cls = int(r[0]); score = float(r[1])
        x1 = max(0.0, float(r[2])); y1 = max(0.0, float(r[3]))
        x2 = min(float(orig_w), float(r[4])); y2 = min(float(orig_h), float(r[5]))
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({
            "cls_id": cls, "label": C.LABELS[cls], "score": score,
            "coordinate": [x1, y1, x2, y2],
        })
    return out


def run_table_cls(session: "ort.InferenceSession",
                  crop_hwc: np.ndarray) -> Tuple[str, np.ndarray]:
    x = cls_inputs(crop_hwc)
    out = session.run(None, {"x": x})
    scores = out[0][0]  # [wired, wireless]
    cls = "wired_table" if int(np.argmax(scores)) == 0 else "wireless_table"
    return cls, scores


def run_cells(sessions: Dict[str, "ort.InferenceSession"],
              crop_hwc: np.ndarray, cls: str) -> List[List[float]]:
    """Cell det on a table crop -> boxes in crop pixel space."""
    ch, cw = crop_hwc.shape[:2]
    image, im_shape, scale_factor = detr_inputs(crop_hwc, 640, cw, ch)
    model = sessions["wired_cell" if cls == "wired_table" else "wireless_cell"]
    boxes_raw, counts = model.run(
        None, {"im_shape": im_shape, "image": image, "scale_factor": scale_factor})
    n = int(counts[0])
    b = boxes_raw[:n]
    if b.size == 0:
        return []
    sel = [i for i in range(len(b)) if float(b[i, 1]) > C.CELL_DET_THRESH and float(b[i, 0]) >= 0]
    b = b[sel]
    if b.size == 0:
        return []
    coords = b[:, 2:].tolist()
    scores = b[:, 1].tolist()
    kept_coords, _ = nms_score(coords, scores, C.CELL_NMS_IOU)
    out = []
    for c in kept_coords:
        x1 = max(0.0, min(float(c[0]), float(cw)))
        y1 = max(0.0, min(float(c[1]), float(ch)))
        x2 = max(0.0, min(float(c[2]), float(cw)))
        y2 = max(0.0, min(float(c[3]), float(ch)))
        if x2 > x1 and y2 > y1:
            out.append([x1, y1, x2, y2])
    return out


def run_ocr_det(session: "ort.InferenceSession",
                img_hwc: np.ndarray) -> Tuple[List[np.ndarray], List[float]]:
    """DB text det -> (dt_polys, dt_scores) in original pixel space.

    Polys are np.int16 (4x2) arrays, PaddleX-compatible.
    """
    r, shape = det_resize(img_hwc)
    src_h, src_w = int(shape[0]), int(shape[1])
    rn = ((r / 255.0 - C.MEAN) / C.STD).transpose(2, 0, 1)[None]
    pred = session.run(None, {"x": rn})[0]
    prob = pred[0, 0]  # (H, W)
    return db_postprocess(prob, src_w, src_h)


def db_postprocess(prob: np.ndarray, dest_w: int, dest_h: int
                   ) -> Tuple[List[np.ndarray], List[float]]:
    """Port of PaddleX DBPostProcess (quad, fast score)."""
    try:
        import pyclipper
    except ImportError:
        return [], []
    segmentation = (prob > C.DET_THRESH).astype(np.uint8)
    h, w = prob.shape
    contours, _ = cv2.findContours((segmentation * 255).astype(np.uint8),
                                   cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_size = 3
    max_cand = C.DET_MAX_CANDIDATES

    def get_mini_boxes(contour):
        bb = cv2.minAreaRect(contour)
        pts = sorted(list(cv2.boxPoints(bb)), key=lambda p: p[0])
        i1, i2, i3, i4 = 0, 1, 2, 3
        if pts[1][1] > pts[0][1]:
            i1, i4 = 0, 1
        else:
            i1, i4 = 1, 0
        if pts[3][1] > pts[2][1]:
            i2, i3 = 2, 3
        else:
            i2, i3 = 3, 2
        box = [pts[i1], pts[i2], pts[i3], pts[i4]]
        return box, min(bb[1])

    def unclip(box, ratio):
        area = cv2.contourArea(box)
        length = cv2.arcLength(box, True)
        dist = area * ratio / length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        try:
            expanded = np.array(offset.Execute(dist))
        except ValueError:
            expanded = np.array(offset.Execute(dist)[0])
        return expanded

    def box_score_fast(_box):
        box = _box.copy()
        xmin = max(0, min(math.floor(box[:, 0].min()), w - 1))
        xmax = max(0, min(math.ceil(box[:, 0].max()), w - 1))
        ymin = max(0, min(math.floor(box[:, 1].min()), h - 1))
        ymax = max(0, min(math.ceil(box[:, 1].max()), h - 1))
        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype(np.int32), 1)
        return cv2.mean(prob[ymin:ymax + 1, xmin:xmax + 1], mask)[0]

    width_scale = dest_w / w
    height_scale = dest_h / h
    boxes, scores = [], []
    for contour in contours[:max_cand]:
        pts, sside = get_mini_boxes(contour)
        if sside < min_size:
            continue
        pts = np.array(pts)
        score = box_score_fast(pts)
        if C.DET_BOX_THRESH > score:
            continue
        box = unclip(pts, C.DET_UNCLIP).reshape(-1, 1, 2)
        box, sside = get_mini_boxes(box)
        if sside < min_size + 2:
            continue
        box = np.array(box)
        for i in range(box.shape[0]):
            box[i, 0] = max(0, min(round(box[i, 0] * width_scale), dest_w))
            box[i, 1] = max(0, min(round(box[i, 1] * height_scale), dest_h))
        boxes.append(box.astype(np.int16))
        scores.append(float(score))
    # PaddleX does not NMS the DB quads (sorts by min-y then min-x for rec).
    order = sorted(range(len(boxes)),
                   key=lambda i: (float(boxes[i][:, 1].min()),
                                  float(boxes[i][:, 0].min())))
    boxes = [boxes[i] for i in order]
    scores = [scores[i] for i in order]
    return boxes, scores


def reading_order(boxes) -> List[int]:
    """Simple xycut: sort by top, then left. Returns index order."""
    if not boxes:
        return []
    return sorted(range(len(boxes)),
                  key=lambda i: (int(boxes[i]["coordinate"][1]),
                                 int(boxes[i]["coordinate"][0])))


# ---------------------------------------------------------------------------
# page-level composition
# ---------------------------------------------------------------------------

def process_page(sessions: Dict[str, "ort.InferenceSession"],
                 img_hwc: np.ndarray,
                 wall_time: Optional[float] = None) -> Tuple[dict, float]:
    """Run the full pipeline on one page image -> (prunedResult dict, elapsed).

    The prunedResult dict is the PaddleX-compatible shape the worker reads via
    result.layoutParsingResults[0].prunedResult. ``envelope`` wraps it.
    """
    t0 = time.perf_counter()
    orig_h, orig_w = img_hwc.shape[:2]
    W = int(orig_w); H = int(orig_h)

    layout_boxes = run_layout(sessions, img_hwc)
    dt_polys, dt_scores = run_ocr_det(sessions["ocrv5_mobile_det"], img_hwc)

    # tables keyed by layout-box index so we can reorder to reading order below.
    table_res_by_idx = {}
    for li, tb in enumerate(layout_boxes):
        if tb["label"] != "table":
            continue
        x1, y1, x2, y2 = [int(v) for v in tb["coordinate"]]
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(W, x2); y2 = min(H, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        crop = img_hwc[y1:y2, x1:x2]
        cls, cls_scores = run_table_cls(sessions["table_cls"], crop)
        cells_crop = run_cells(sessions, crop, cls)
        cells_orig = [[c[0] + x1, c[1] + y1, c[2] + x1, c[3] + y1]
                      for c in cells_crop]
        tab_ocr = {"rec_polys": [], "rec_texts": [], "rec_scores": [], "rec_boxes": []}
        table_res_by_idx[li] = {
            "cell_box_list": cells_orig,
            "pred_html": "",
            "table_ocr_pred": tab_ocr,
            "_cls": cls,
            "_table_box": [x1, y1, x2, y2],
        }

    # parsing_res_list in reading order; table_res_list in the SAME order so
    # the worker's positional pairing is correct.
    order = reading_order(layout_boxes)
    parsing_res_list = []
    table_res_list = []
    visualize_order = 0
    for li in order:
        box = layout_boxes[li]
        label = box["label"]
        x1 = max(0, min(int(round(box["coordinate"][0])), W))
        y1 = max(0, min(int(round(box["coordinate"][1])), H))
        x2 = max(0, min(int(round(box["coordinate"][2])), W))
        y2 = max(0, min(int(round(box["coordinate"][3])), H))
        bbox = [x1, y1, x2, y2]
        if label == "table":
            content = ""  # VLM gateway overwrites table HTML
            tr = table_res_by_idx.get(li)
            if tr is not None:
                table_res_list.append(tr)
        else:
            content = ""
        if label in C.VISUALIZE_ORDER_LABELS:
            block_order = visualize_order
            visualize_order += 1
        else:
            block_order = None
        parsing_res_list.append({
            "block_label": label,
            "block_content": content,
            "block_bbox": bbox,
            "block_id": len(parsing_res_list),
            "block_order": block_order,
        })

    overall_ocr_res = {
        "model_settings": {"use_doc_preprocessor": False,
                           "use_textline_orientation": True},
        "dt_polys": [np.asarray(p, dtype=int).tolist() for p in dt_polys],
        "text_det_params": {
            "limit_side_len": C.DET_RESIZE_LIMIT, "limit_type": "min",
            "thresh": C.DET_THRESH, "max_side_limit": C.DET_MAX_SIDE,
            "box_thresh": C.DET_BOX_THRESH, "unclip_ratio": C.DET_UNCLIP,
        },
        "text_type": "general",
        "textline_orientation_angles": [0] * len(dt_polys),
        "text_rec_score_thresh": 0.0,
        "return_word_box": False,
        "rec_texts": [],
        "rec_scores": [None] * len(dt_polys),
        "rec_polys": [np.asarray(p, dtype=int).tolist() for p in dt_polys],
        "rec_boxes": [[float(min(q[0] for q in p)),
                       float(min(q[1] for q in p)),
                       float(max(q[0] for q in p)),
                       float(max(q[1] for q in p))] for p in dt_polys],
    }

    pruned = {
        "width": W,
        "height": H,
        "model_settings": {
            "use_doc_preprocessor": False, "use_seal_recognition": False,
            "use_table_recognition": True, "use_formula_recognition": False,
            "use_chart_recognition": False, "use_region_detection": True,
            "format_block_content": False,
            "markdown_ignore_labels": list(C.MARKDOWN_IGNORE_LABELS),
        },
        "parsing_res_list": parsing_res_list,
        "layout_det_res": {"boxes": layout_boxes},
        "overall_ocr_res": overall_ocr_res,
        "table_res_list": [
            {"cell_box_list": t["cell_box_list"], "pred_html": t["pred_html"],
             "table_ocr_pred": t["table_ocr_pred"]}
            for t in table_res_list
        ],
        "_table_cls": [
            {"table_box": t["_table_box"], "cls": t["_cls"]} for t in table_res_list
        ],
    }
    return pruned, (wall_time or (time.perf_counter() - t0))


def envelope(pruned: dict) -> dict:
    """Wrap prunedResult in the PaddleX response envelope the worker reads."""
    clean = {k: v for k, v in pruned.items() if not k.startswith("_")}
    return {"result": {"layoutParsingResults": [{"prunedResult": clean}]}}


# ---------------------------------------------------------------------------
# the only I/O boundary: model loading / session construction
# ---------------------------------------------------------------------------

def load_sessions(model_dir: str,
                  providers: Optional[Sequence[str]] = None,
                  provider_options: Optional[Sequence[dict]] = None,
                  device_id: int = 0,
                  assert_gpu: bool = False,
                  mem_cap: Optional[bool] = None) -> Dict[str, "ort.InferenceSession"]:
    """Construct the five InferenceSessions. The only place models are read.

    ``assert_gpu`` refuses to start if the CUDA EP did not actually run a real
    inference through the layout model. ``mem_cap`` (or ORT_MEM_CAP=1) enables
    per-model GPU memory caps for memory-tight hosts.
    """
    providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if mem_cap is None:
        mem_cap = os.environ.get("ORT_MEM_CAP", "0") == "1"

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 2  # warnings
    # Variable input sizes make ORT's memory-pattern optimization thrash the BFC
    # arena across pages; per-run allocation avoids spurious allocation failures.
    so.enable_mem_pattern = False

    sessions: Dict[str, "ort.InferenceSession"] = {}
    for name in C.MODEL_NAMES:
        if mem_cap:
            cuda_opt = {"device_id": device_id,
                        "gpu_mem_limit": C.MEM_CAPS[name]}
            popts = [cuda_opt, {}]
        elif provider_options is not None:
            popts = provider_options
        else:
            # One entry per provider, aligned by position. For a single CPU EP
            # this is [{}]; a two-element list here would make ORT reject the
            # (providers, provider_options) pair and silently fall back.
            popts = [{}] * len(providers or ["CUDAExecutionProvider", "CPUExecutionProvider"])
        sessions[name] = ort.InferenceSession(
            os.path.join(model_dir, name + ".onnx"),
            sess_options=so, providers=providers,
            provider_options=popts)

    if assert_gpu:
        _assert_gpu(sessions)
    return sessions


def _assert_gpu(sessions: Dict[str, "ort.InferenceSession"]) -> None:
    """Refuse to serve if the CUDA EP did not actually run a real inference."""
    for name, sess in sessions.items():
        if "CUDAExecutionProvider" not in sess.get_providers():
            raise RuntimeError(
                f"{name}: CUDAExecutionProvider not in "
                f"{sess.get_providers()}; refusing to start without GPU.")
    # Real inference on a dummy 800x800 tensor through the layout model.
    img = np.zeros((3, 800, 800), dtype=np.float32)
    im_shape = np.array([[800, 800]], dtype=np.float32)
    scale_factor = np.array([[1.0, 1.0]], dtype=np.float32)
    out = sessions["doclayout_plusL"].run(
        None, {"im_shape": im_shape, "image": img[None], "scale_factor": scale_factor})
    if out[0].size == 0:
        raise RuntimeError("CUDA sanity inference produced no output")


class Pipeline:
    """Thin wrapper over load_sessions + the pure pipeline functions.

    Constructs and holds the InferenceSessions; every
    method delegates to the pure functions above. Nothing in this class does
    business logic.
    """

    def __init__(self, model_dir: Optional[str] = None,
                 providers: Optional[Sequence[str]] = None,
                 provider_options: Optional[Sequence[dict]] = None,
                 device_id: int = 0,
                 assert_gpu: bool = False,
                 mem_cap: Optional[bool] = None):
        self.model_dir = model_dir if model_dir is not None else C.MODEL_DIR
        self.sessions = load_sessions(
            self.model_dir, providers, provider_options, device_id,
            assert_gpu, mem_cap)
        self.get_providers = {n: s.get_providers() for n, s in self.sessions.items()}

    def run_layout(self, img_hwc: np.ndarray) -> List[dict]:
        return run_layout(self.sessions, img_hwc)

    def run_table_cls(self, crop_hwc: np.ndarray) -> Tuple[str, np.ndarray]:
        return run_table_cls(self.sessions["table_cls"], crop_hwc)

    def run_cells(self, crop_hwc: np.ndarray, cls: str) -> List[List[float]]:
        return run_cells(self.sessions, crop_hwc, cls)

    def run_ocr_det(self, img_hwc: np.ndarray) -> Tuple[List[np.ndarray], List[float]]:
        return run_ocr_det(self.sessions["ocrv5_mobile_det"], img_hwc)

    def process_page(self, img_hwc: np.ndarray,
                     wall_time: Optional[float] = None) -> Tuple[dict, float]:
        return process_page(self.sessions, img_hwc, wall_time)

    def envelope(self, pruned: dict) -> dict:
        return envelope(pruned)
