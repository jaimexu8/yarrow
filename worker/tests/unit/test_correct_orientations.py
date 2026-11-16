"""Scoping of DocumentParser.correct_orientations by pages_to_process."""

import pytest

from app.pipeline.document_parser import DocumentParser


class TestCorrectOrientationsScoping:
    @pytest.fixture
    def parser(self):
        parser = DocumentParser()
        # correct_page is stubbed, so the page content never gets decoded.
        parser.pages = ["page_0", "page_1"]
        calls: list[int] = []

        def correct_page(page_b64):
            calls.append(parser.pages.index(page_b64))
            return page_b64, 0.0, 0.0

        parser.orientation_corrector.correct_page = correct_page
        parser.calls = calls
        return parser

    def test_full_run_detects_every_page(self, parser):
        """A None scope covers the whole document."""
        parser.correct_orientations(None)
        assert parser.calls == [0, 1]

    def test_partial_run_detects_only_the_named_pages(self, parser):
        """Only pages in pages_to_process are detected; the rest are left alone."""
        parser.correct_orientations((1,))
        assert parser.calls == [0]

    def test_manual_override_still_skips_auto_detection(self, parser):
        """A page with a manual rotation is never auto-detected, even on a full run."""
        parser.manual_rotations = {0: 90}
        parser.correct_orientations(None)
        assert parser.calls == [1]
