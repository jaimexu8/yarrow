import base64
import io
import logging

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


class OrientationCorrector:
    """Detects a page's content rotation and returns the corrected page.

    A page is rotated by rotating its rendered content, so the detector works
    on the raster rather than on file metadata. The estimate is continuous and
    resolves the 180-degree ambiguity by preferring the orientation whose
    ink is top-heavy. A page is only corrected when it has enough ink, the
    projection profile is unambiguous, and the estimated rotation is larger
    than MIN_ROTATION_DEG; everything else is returned unchanged.
    """

    DETECT_WIDTH = 800          # pages are downscaled to this width before profiling
    MIN_ROTATION_DEG = 2.0      # below this, the page is considered upright
    MIN_INK_RATIO = 0.005       # ink area over page area below this is too sparse to trust
    # Peak-to-second-peak projection variance. A real page of text has sharp,
    # dominant horizontal lines, so its row profile is unambiguous (variance
    # ~1400-3000 at 800px width). Blur, sparse or photo-scan pages have no
    # dominant line angle, so a low variance means the estimate is noise and
    # must not be trusted.
    MIN_PROJECTION_MARGIN = 1000.0
    # Fraction of near-white pixels. The top-heavy 180-degree flip is only
    # trustworthy on clean white paper. On a photo of paper the desk, shadow
    # and gray paper read as "bottom-heavy", so the flip would turn upright
    # pages upside down; it is suppressed below this threshold.
    MIN_NEAR_WHITE = 0.90

    def correct_page(self, page_b64: str) -> tuple[str, float, float]:
        """Return (corrected_b64, content_rotation_deg, confidence).

        content_rotation_deg is the angle the page's content was rotated by
        (CCW positive), 0.0 when the page is left unchanged. The correction
        applied to the image is its negative. confidence is in [0, 1] and 0.0
        for a page that was not corrected.
        """
        try:
            img = self._decode(page_b64)
            if img is None:
                return page_b64, 0.0, 0.0

            # Phone-camera scans carry an orientation tag; honour it before
            # anything else. The transpose is a no-op when the tag is absent
            # or 1, so the tag itself says whether the page will differ from
            # its input even with no rotation applied.
            exif_applied = img.getexif().get(0x0112) not in (None, 1)
            img = ImageOps.exif_transpose(img)

            small = self._downscale(img)

            angle, margin = self._estimate_rotation(small)

            if not self._is_reliable(margin):
                return self._unchanged(page_b64, img, exif_applied), 0.0, 0.0

            flip, upright_gap = self._upright_scores(small, angle)
            if flip and self._near_white_fraction(small) < self.MIN_NEAR_WHITE:
                # The mod-180 estimate is ambiguous: the page is either
                # rotated by angle or by angle + 180. The upright scorer
                # picked the latter, but only on clean white paper can we
                # trust that pick. On a photo of paper the desk, shadow and
                # gray tone read as "bottom-heavy", so the flip would turn an
                # upright page upside down. We still apply a tilt-only
                # correction for those (the angle is trustworthy); we just
                # never 180-degree-flip a non-white page.
                flip = False
            if flip:
                angle = (angle + 180.0) % 360.0
                if angle > 180.0:
                    angle -= 360.0

            if abs(angle) < self.MIN_ROTATION_DEG:
                return self._unchanged(page_b64, img, exif_applied), 0.0, 0.0

            corrected = self._rotate(img, -angle)
            return self._reencode(corrected), angle, min(1.0, upright_gap * 2.0)
        except Exception:
            # A detection failure only loses its correction, never the page.
            logger.exception("Orientation correction failed; leaving the page unchanged")
            return page_b64, 0.0, 0.0

    @staticmethod
    def _unchanged(page_b64: str, img: Image.Image, exif_applied: bool) -> str:
        # An EXIF-transposed page is still a corrected page, so it is
        # re-encoded; a page left entirely alone keeps its original bytes.
        return page_b64 if not exif_applied else OrientationCorrector._reencode(img)

    @staticmethod
    def _decode(page_b64: str) -> Image.Image | None:
        try:
            img = Image.open(io.BytesIO(base64.b64decode(page_b64)))
            img.load()
            return img
        except Exception:
            return None

    @staticmethod
    def _downscale(img: Image.Image) -> np.ndarray:
        gray = np.array(img.convert("L"))
        scale = OrientationCorrector.DETECT_WIDTH / gray.shape[1]
        if scale < 1.0:
            gray = cv2.resize(gray, (OrientationCorrector.DETECT_WIDTH, max(1, round(gray.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        return gray

    def _is_reliable(self, margin: float) -> bool:
        return margin >= self.MIN_PROJECTION_MARGIN

    @staticmethod
    def _near_white_fraction(gray: np.ndarray) -> float:
        """The share of pixels that read as white paper, at the detection size.

        A clean generated or photographed-on-white page is ~95% near-white; a
        photo of gray paper on a desk is ~0%. The 180-degree flip is only
        trusted when this fraction is high (see correct_page).
        """
        return float((gray > 220).mean())

    def _estimate_rotation(self, gray: np.ndarray) -> tuple[float, float]:
        """The content rotation (mod 180, CCW positive) and the projection margin.

        For each candidate correction the ink mask is rotated and the variance
        of its horizontal projection is measured. Upright text lines are
        horizontal, and a horizontal row of ink produces the sharpest,
        highest-variance row profile, so the peak of the sweep is the page's
        line angle.
        """
        ink = (cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1] > 0).astype(np.uint8)
        area = ink.sum()
        if area / ink.size < self.MIN_INK_RATIO:
            return 0.0, 0.0

        center = (gray.shape[1] / 2, gray.shape[0] / 2)

        def profile_variance(mask: np.ndarray) -> float:
            return float(np.var(mask.sum(axis=1).astype(np.float64)))

        coarse = [
            (c, profile_variance(self._rotate_mask(ink, c, center)))
            for c in range(-180, 180)
        ]
        peak_c, _ = max(coarse, key=lambda pair: pair[1])

        fine = [
            (c, profile_variance(self._rotate_mask(ink, c, center)))
            for c in np.arange(peak_c - 1, peak_c + 1.1, 0.1)
        ]
        best_c, best_v = max(fine, key=lambda pair: pair[1])

        # Fold the best correction back into the content rotation: content
        # rotated by theta is made horizontal by rotating it by -theta, so
        # theta = -best_c (mod 180), mapped to (-180, 180].
        angle = (-float(best_c)) % 180.0
        if angle > 90.0:
            angle -= 180.0

        # Margin against the best other candidate at 1-degree resolution,
        # excluding the 180-degree twin of the peak (which is equally valid).
        twin = (peak_c + 180) % 360
        if twin > 180:
            twin -= 360
        second = max((v for c, v in coarse if c not in (peak_c, twin)), default=0.0)
        return angle, best_v - second

    @staticmethod
    def _rotate_mask(mask: np.ndarray, angle_deg: float, center: tuple[float, float]) -> np.ndarray:
        matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
        return cv2.warpAffine(mask, matrix, (mask.shape[1], mask.shape[0]), flags=cv2.INTER_NEAREST, borderValue=0)

    @staticmethod
    def _rotate(img: Image.Image, angle_deg: float) -> Image.Image:
        # Expand=True keeps the whole page; the border is filled white so the
        # page stays a scan-like white sheet.
        return img.rotate(angle_deg, expand=True, fillcolor=(255, 255, 255))

    @staticmethod
    def _reencode(img: Image.Image) -> str:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _upright_scores(self, gray: np.ndarray, angle: float) -> tuple[bool, float]:
        """Score the two candidate uprights for a page rotated by angle.

        For each of the two corrections that make the lines horizontal, the
        page is rotated onto an expanded white canvas (nothing is clipped) and
        scored by how top-heavy its ink is: the ink-weighted vertical centroid
        relative to the page centre, plus the ink share of the top third over
        the bottom third. A document's densest content (title, headings, body)
        sits in its top, so the higher-scoring candidate is the upright one.

        Returns (flip, gap): flip is True when the second candidate (angle +
        180) scores higher, gap is the difference between the two scores; a
        large gap means the decision is confident.
        """
        scores = []
        for correction in (-angle, -angle + 180.0):
            rotated = self._rotate_expand(gray, correction)
            ink = (255 - rotated).astype(np.float64) / 255.0
            total = ink.sum()
            if total == 0:
                scores.append(0.0)
                continue
            h = rotated.shape[0]
            rows = ink.sum(axis=1)
            ys = np.arange(h, dtype=np.float64)
            centroid = float(np.dot(ys, rows) / total)
            centroid_score = 0.5 - centroid / h  # 0.5 when centred, +0.5 when all ink is at the top
            top_third = float(rows[: h // 3].sum() / total)
            bottom_third = float(rows[2 * h // 3 :].sum() / total)
            scores.append(centroid_score + (top_third - bottom_third))
        return scores[1] > scores[0], max(scores) - min(scores)

    @staticmethod
    def _rotate_expand(gray: np.ndarray, angle_deg: float) -> np.ndarray:
        """Rotate onto an expanded canvas large enough to hold the whole page."""
        h, w = gray.shape[:2]
        center = (w / 2, h / 2)
        matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
        cos = abs(matrix[0, 0])
        sin = abs(matrix[0, 1])
        new_w = int(h * sin + w * cos)
        new_h = int(h * cos + w * sin)
        matrix[0, 2] += new_w / 2 - center[0]
        matrix[1, 2] += new_h / 2 - center[1]
        return cv2.warpAffine(
            gray, matrix, (new_w, new_h), flags=cv2.INTER_AREA, borderValue=255
        )
