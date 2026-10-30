import base64
import json
import os
import warnings

import pytest

# PyMuPDF emits a FutureWarning at import time ("use import pymupdf instead").
# Silence it so the module import stays clean under pytest's warning filters.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import fitz

from app.pipeline.orientation import OrientationCorrector

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DOCS_DIR = os.path.join(HERE, "..", "integration", "test_docs")
PDF_PATH = os.path.join(TEST_DOCS_DIR, "ocr_rotation_test.pdf")
GROUND_TRUTH_PATH = os.path.join(TEST_DOCS_DIR, "rotation_test_json.json")

DEG_TOLERANCE = 2.0
REDISPLAY_TOLERANCE = 3.0


def _deg_diff(a, b):
    """Smallest absolute difference between two angles, in degrees (mod 360)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


@pytest.fixture(scope="module")
def rotation_fixture():
    """Render every page of the rotation test PDF at 150 dpi, once."""
    with open(GROUND_TRUTH_PATH) as handle:
        ground_truth = {entry["page"]: entry["content_rotation_ccw_deg"] for entry in json.load(handle)}

    doc = fitz.open(PDF_PATH)
    pages = []
    for index in range(len(doc)):
        pixmap = doc[index].get_pixmap(dpi=150)
        page_b64 = base64.b64encode(pixmap.tobytes("png")).decode("utf-8")
        pages.append((index + 1, page_b64))
    doc.close()

    return {"pages": pages, "ground_truth": ground_truth}


class TestRealDocumentOrientation:
    """Runs the corrector on a real multi-orientation PDF, no services needed."""

    def test_every_page_matches_ground_truth(self, rotation_fixture):
        """Each page's detected content angle matches its ground-truth angle."""
        corrector = OrientationCorrector()
        for page_number, page_b64 in rotation_fixture["pages"]:
            expected = rotation_fixture["ground_truth"][page_number]
            _, reported, _ = corrector.correct_page(page_b64)

            assert _deg_diff(reported, expected) <= DEG_TOLERANCE, (
                f"page {page_number}: expected ~{expected} deg, got {reported}"
            )

    def test_corrected_pages_are_upright(self, rotation_fixture):
        """Re-detecting a corrected page reports about 0 degrees."""
        corrector = OrientationCorrector()
        for page_number, page_b64 in rotation_fixture["pages"]:
            corrected, _reported, _ = corrector.correct_page(page_b64)
            if corrected == page_b64:
                # An unchanged page is already upright; nothing to re-check.
                continue

            _, redetected, _ = corrector.correct_page(corrected)
            assert _deg_diff(redetected, 0.0) <= REDISPLAY_TOLERANCE, (
                f"page {page_number}: corrected page re-detected as {redetected} deg"
            )
