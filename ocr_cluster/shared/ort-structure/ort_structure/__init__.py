"""ort_structure: pure Paddle-free PP-StructureV3 geometry pipeline (ONNX).

Public surface: the constants, the pure pipeline functions, and the thin
session-loading boundary (``load_sessions`` / ``Pipeline``). The FastAPI server
module (``ort_structure.server``) is a separate, thin wrapper over these and is
not imported here so the pure pipeline stays importable without FastAPI.
"""
from __future__ import annotations

from . import constants
from .constants import (
    LABELS,
    IMAGE_INDEX,
    THRESHOLDS,
    MERGE_MODE,
    MEAN,
    STD,
    DET_RESIZE_LIMIT,
    DET_MAX_SIDE,
    DET_THRESH,
    DET_BOX_THRESH,
    DET_UNCLIP,
    DET_MAX_CANDIDATES,
    DET_NMS_IOU,
    CELL_DET_THRESH,
    CELL_NMS_IOU,
    LAYOUT_NMS_IOU_SAME,
    LAYOUT_NMS_IOU_DIFF,
    MODEL_DIR,
    MODEL_NAMES,
    MEM_CAPS,
    VISUALIZE_ORDER_LABELS,
    MARKDOWN_IGNORE_LABELS,
)
from .pipeline import (
    detr_inputs,
    cls_inputs,
    det_resize,
    iou,
    nms_layout,
    nms_score,
    is_contained,
    filter_large_image,
    merge_bboxes,
    run_layout,
    run_table_cls,
    run_cells,
    run_ocr_det,
    db_postprocess,
    reading_order,
    process_page,
    envelope,
    load_sessions,
    Pipeline,
)

__all__ = [
    # constants
    "constants", "LABELS", "IMAGE_INDEX", "THRESHOLDS", "MERGE_MODE",
    "MEAN", "STD", "DET_RESIZE_LIMIT", "DET_MAX_SIDE", "DET_THRESH",
    "DET_BOX_THRESH", "DET_UNCLIP", "DET_MAX_CANDIDATES", "DET_NMS_IOU",
    "CELL_DET_THRESH", "CELL_NMS_IOU", "LAYOUT_NMS_IOU_SAME",
    "LAYOUT_NMS_IOU_DIFF", "MODEL_DIR", "MODEL_NAMES", "MEM_CAPS",
    "VISUALIZE_ORDER_LABELS", "MARKDOWN_IGNORE_LABELS",
    # pure functions
    "detr_inputs", "cls_inputs", "det_resize", "iou", "nms_layout",
    "nms_score", "is_contained", "filter_large_image", "merge_bboxes",
    "run_layout", "run_table_cls", "run_cells", "run_ocr_det",
    "db_postprocess", "reading_order", "process_page", "envelope",
    # boundary
    "load_sessions", "Pipeline",
]
