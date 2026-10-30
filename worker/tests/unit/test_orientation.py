import base64
import io

import pytest
from PIL import Image, ImageDraw

from app.pipeline.orientation import OrientationCorrector

# A deterministic stand-in for a text page: a thick, distinct title bar and a
# short subtitle at the very top, then several thinner body lines, on a white
# background. The heavy top keeps the 180-degree flip unambiguous, and the
# white background keeps the near-white flip gate (>=0.90) satisfied.


def _bar_page(size=(800, 1000)):
    """Build the upright bar page as a PIL image (white background, dark bars)."""
    width, height = size
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    # Distinct, dense title block: a thick title bar plus a shorter subtitle.
    draw.rectangle([50, 40, 50 + 560, 40 + 24], fill="black")
    draw.rectangle([50, 40 + 24 + 12, 50 + int(560 * 0.7), 40 + 24 + 12 + 12], fill="black")
    # Body lines below; the bottom third stays empty so the page is top-heavy.
    y = 120
    for _ in range(5):
        draw.rectangle([50, y, 50 + 560, y + 10], fill="black")
        y += 10 + 50
    return img


def _to_b64(img):
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _rotated_b64(angle):
    """The bar page rotated by `angle` degrees (CCW) and encoded to base64."""
    img = _bar_page()
    if angle:
        img = img.rotate(angle, expand=True, fillcolor="white")
    return _to_b64(img)


def _deg_diff(a, b):
    """Smallest absolute difference between two angles, in degrees (mod 360)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


class TestOrientationCorrector:
    """Unit tests for OrientationCorrector.correct_page, on synthetic bar pages."""

    corrector = OrientationCorrector()

    @pytest.mark.parametrize("angle", [90, 180, 270, 20, -20])
    def test_detects_known_rotation(self, angle):
        """A page rotated by a known angle is reported with that content angle."""
        b64 = _rotated_b64(angle)
        _, reported, confidence = self.corrector.correct_page(b64)

        assert _deg_diff(reported, angle) <= 3.0, f"expected ~{angle} deg, got {reported}"
        assert confidence > 0.0

    def test_upright_page_is_unchanged(self):
        """An already-upright page reports 0 degrees and returns the original bytes."""
        b64 = _rotated_b64(0)
        corrected, reported, confidence = self.corrector.correct_page(b64)

        assert reported == 0.0
        assert confidence == 0.0
        assert corrected == b64

    def test_corrected_output_is_idempotent(self):
        """A corrected page re-classifies as upright (about 0 degrees)."""
        b64 = _rotated_b64(20)
        corrected, _, _ = self.corrector.correct_page(b64)
        assert corrected != b64

        _, redetected, _ = self.corrector.correct_page(corrected)
        assert _deg_diff(redetected, 0.0) <= 3.0

    def test_all_white_page_is_not_rotated(self):
        """A near-empty (all white) page has no ink to trust, so it is left alone."""
        img = Image.new("RGB", (800, 1000), "white")
        b64 = _to_b64(img)

        corrected, reported, confidence = self.corrector.correct_page(b64)

        assert reported == 0.0
        assert confidence == 0.0
        assert corrected == b64

    def test_garbage_base64_returns_original_without_raising(self):
        """A decode failure leaves the page unchanged and does not raise."""
        garbage = "not valid base64 image data"
        corrected, reported, confidence = self.corrector.correct_page(garbage)

        assert corrected == garbage
        assert reported == 0.0
        assert confidence == 0.0

    def test_low_ink_page_is_left_alone(self):
        """A page with very little ink is below the ink threshold and is left alone."""
        img = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(img)
        for (x, y) in ((100, 100), (300, 500), (500, 800)):
            draw.rectangle([x, y, x + 20, y + 12], fill="black")
        b64 = _to_b64(img)

        corrected, reported, confidence = self.corrector.correct_page(b64)

        assert reported == 0.0
        assert confidence == 0.0
        assert corrected == b64

    def test_exif_orientation_is_honored(self):
        """A JPEG carrying an EXIF orientation tag is transposed to the upright view."""
        # Orientation 6 means the stored image is rotated 90 degrees CW, so a
        # tall stored image is meant to be displayed wide.
        img = Image.new("RGB", (300, 400), "white")
        exif = img.getexif()
        exif[0x0112] = 6
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", exif=exif.tobytes())
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # Sanity: the stored JPEG is tall and still carries the tag.
        stored = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert stored.size == (300, 400)
        assert stored.getexif().get(0x0112) == 6

        corrected, reported, confidence = self.corrector.correct_page(b64)

        out = Image.open(io.BytesIO(base64.b64decode(corrected)))
        # The 90-degree transpose makes the tall image come back wide.
        assert out.size == (400, 300)
        # No content rotation is applied, only the EXIF transpose.
        assert reported == 0.0
        assert confidence == 0.0
        # The corrected image no longer needs the tag (transposed away).
        assert out.getexif().get(0x0112) in (None, 1)
