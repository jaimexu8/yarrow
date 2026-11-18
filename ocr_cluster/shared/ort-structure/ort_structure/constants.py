"""Module-level constants for the ORT structure pipeline.

Do not change any value here without re-running the parity gate:
the numerics are parity-verified on x86 GPU and GB10 (layout recall 0.986, cells 0.986).
"""
from __future__ import annotations

import os

import numpy as np

# 20 layout labels in label_list order (index = class_id).
LABELS = [
    "paragraph_title", "image", "text", "number", "abstract", "content",
    "figure_title", "formula", "table", "reference", "doc_title", "footnote",
    "header", "algorithm", "footer", "seal", "chart", "formula_number",
    "aside_text", "reference_content",
]

# Class id of the "image" label in LABELS (used by the large-image filter).
IMAGE_INDEX = LABELS.index("image")

# Per-class score thresholds from the live PP-StructureV3 config.
THRESHOLDS = {
    0: 0.3, 1: 0.5, 2: 0.4, 3: 0.5, 4: 0.5, 5: 0.5, 6: 0.5, 7: 0.3, 8: 0.5,
    9: 0.5, 10: 0.5, 11: 0.5, 12: 0.5, 13: 0.5, 14: 0.5, 15: 0.45, 16: 0.5,
    17: 0.5, 18: 0.5, 19: 0.5,
}
# layout_merge_bboxes_mode per class (large/union/small) from the live config.
MERGE_MODE = {
    0: "large", 1: "large", 2: "union", 3: "union", 4: "union", 5: "union",
    6: "union", 7: "large", 8: "union", 9: "union", 10: "union", 11: "union",
    12: "union", 13: "union", 14: "union", 15: "union", 16: "large",
    17: "union", 18: "union", 19: "union",
}

# ImageNet stats (RGB order, as PaddleX uses).
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# OCR det params (live pipeline overrides the model yml's resize_long=960).
DET_RESIZE_LIMIT = 736          # limit_side_len, limit_type=min
# On a memory-tight host lower
# ORT_DET_MAX_SIDE to cap the input side
DET_MAX_SIDE = int(os.environ.get("ORT_DET_MAX_SIDE", "4000"))
DET_THRESH = 0.3                # binarization threshold
DET_BOX_THRESH = 0.6            # box score threshold
DET_UNCLIP = 1.5                # unclip ratio
DET_MAX_CANDIDATES = 1000
DET_NMS_IOU = 0.7               # (present for reference; PaddleX does not NMS the DB quads)

CELL_DET_THRESH = 0.3           # live hardcoded threshold
CELL_NMS_IOU = 0.3              # PaddleX cells_det_results_nms default

LAYOUT_NMS_IOU_SAME = 0.6
LAYOUT_NMS_IOU_DIFF = 0.98

# Where the ONNX models live
MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/ort/models")

# The five models this service loads (V2/V3 layout, rec, textline-ori and the
# SLANeXt/SLANet table-recognition models are intentionally NOT loaded).
MODEL_NAMES = [
    "doclayout_plusL", "table_cls", "wired_cell", "wireless_cell",
    "ocrv5_mobile_det",
]

# Per-model GPU memory cap (bytes), enabled only when ORT_MEM_CAP=1 (a
# memory-tight safety device, off by default).
MEM_CAPS = {
    "doclayout_plusL": int(0.95e9),
    "table_cls": int(0.20e9),
    "wired_cell": int(0.50e9),
    "wireless_cell": int(0.50e9),
    "ocrv5_mobile_det": int(1.50e9),
}

# Labels that receive a running block_order counter (PaddleX
# visualize_index_labels), minus the markdown_ignore_labels from the live
# config.
_MARKDOWN_IGNORE = {"number", "footnote", "header", "header_image",
                    "footer", "footer_image", "aside_text"}
_VISUALIZE_ORDER_LABELS = [
    "text", "formula", "algorithm", "reference", "content", "abstract",
    "paragraph_title", "doc_title", "abstract_title", "refer_title",
    "content_title", "number", "footnote", "header", "header_image",
    "footer", "footer_image", "aside_text",
]
VISUALIZE_ORDER_LABELS = [l for l in _VISUALIZE_ORDER_LABELS
                          if l not in _MARKDOWN_IGNORE]

# markdown_ignore_labels as emitted in the prunedResult model_settings.
MARKDOWN_IGNORE_LABELS = ["number", "footnote", "header", "header_image",
                          "footer", "footer_image", "aside_text"]
