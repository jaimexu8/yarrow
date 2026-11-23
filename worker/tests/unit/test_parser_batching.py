"""TDD tests for page counting and partial page loading.

Every test below calls an API that Phase 5 is meant to add, so this module
currently fails on missing attributes rather than on test setup or typos.
"""

import base64
import io
import warnings
from pathlib import Path

import pytest
from PIL import Image

# PyMuPDF warns at import time about its deprecated module name; existing unit
# tests suppress the same warning, so keep collection output clean.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import fitz

from app.pipeline.document_parser import DocumentParser
from app.pipeline.file_loaders.file_loader import FileLoader
from app.pipeline.file_loaders.pdf_loader import PDFLoader
from app.pipeline.file_loaders.png_loader import PNGLoader

TEST_DOCS = Path(__file__).resolve().parents[1] / "integration" / "test_docs"

THREE_PAGE_PDF = (TEST_DOCS / "three_page.pdf").read_bytes()
SINGLE_PAGE_PNG = (TEST_DOCS / "single_page.png").read_bytes()
UNSUPPORTED = (TEST_DOCS / "unsupported.txt").read_bytes()


def decoded_pixels(page_b64: str) -> bytes:
    """Decode a base64 PNG page and return its raw RGB pixels."""
    with Image.open(io.BytesIO(base64.b64decode(page_b64, validate=True))) as image:
        assert image.format == "PNG"
        image.load()
        return image.convert("RGB").tobytes()


def rendered_pixels(pdf_data: bytes, page_number: int) -> bytes:
    """Rasterize one PDF page the same way the loader is specified to."""
    document = fitz.open(stream=pdf_data, filetype="pdf")
    try:
        pixmap = document[page_number - 1].get_pixmap(dpi=150)
        with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
            image.load()
            return image.convert("RGB").tobytes()
    finally:
        document.close()


def test_count_pages_pdf():
    parser = DocumentParser()
    assert parser.count_pages(THREE_PAGE_PDF) == 3


def test_count_pages_single_page_image():
    parser = DocumentParser()
    assert parser.count_pages(SINGLE_PAGE_PNG) == 1


def test_count_pages_unsupported_raises_value_error():
    parser = DocumentParser()
    with pytest.raises(ValueError):
        parser.count_pages(UNSUPPORTED)


def test_load_pages_subset_is_full_length_in_document_order():
    # Caller order is [3, 1]; the pages list must stay in document order.
    parser = DocumentParser()
    parser.load_pages(THREE_PAGE_PDF, [3, 1])

    assert len(parser.pages) == 3
    assert parser.count_pages(THREE_PAGE_PDF) == len(parser.pages)
    assert parser.pages[1] == ""
    assert decoded_pixels(parser.pages[0]) == rendered_pixels(THREE_PAGE_PDF, 1)
    assert decoded_pixels(parser.pages[2]) == rendered_pixels(THREE_PAGE_PDF, 3)


def test_load_pages_single_page_image():
    parser = DocumentParser()
    parser.load_pages(SINGLE_PAGE_PNG, [1])

    assert parser.pages == [base64.b64encode(SINGLE_PAGE_PNG).decode("utf-8")]


@pytest.mark.parametrize("page_numbers", [[0], [2]])
def test_load_pages_out_of_range_pages_leave_all_pages_empty(page_numbers):
    parser = DocumentParser()
    parser.load_pages(SINGLE_PAGE_PNG, page_numbers)

    assert len(parser.pages) == parser.count_pages(SINGLE_PAGE_PNG)
    assert parser.pages == [""]


def test_pdf_loader_count_pages():
    assert PDFLoader().count_pages(THREE_PAGE_PDF) == 3


def test_pdf_loader_process_pages_only_renders_requested_pages():
    pages = PDFLoader().process_pages(THREE_PAGE_PDF, [3, 1])

    assert len(pages) == 3
    assert pages[1] == ""
    assert decoded_pixels(pages[0]) == rendered_pixels(THREE_PAGE_PDF, 1)
    assert decoded_pixels(pages[2]) == rendered_pixels(THREE_PAGE_PDF, 3)


def test_png_loader_inherits_base_count_pages():
    loader = PNGLoader()
    assert isinstance(loader, FileLoader)
    assert loader.count_pages(SINGLE_PAGE_PNG) == 1


def test_png_loader_inherits_base_process_pages():
    loader = PNGLoader()
    assert isinstance(loader, FileLoader)

    expected = base64.b64encode(SINGLE_PAGE_PNG).decode("utf-8")
    assert loader.process_pages(SINGLE_PAGE_PNG, [1]) == [expected]
    assert loader.process_pages(SINGLE_PAGE_PNG, [2]) == [""]


def test_load_pages_non_empty_pages_round_trip_through_pil():
    parser = DocumentParser()
    parser.load_pages(THREE_PAGE_PDF, [1, 2])

    assert len(parser.pages) == 3
    for page_b64 in parser.pages:
        if not page_b64:
            continue
        with Image.open(io.BytesIO(base64.b64decode(page_b64, validate=True))) as image:
            assert image.format == "PNG"
            image.load()
            assert image.width > 0
            assert image.height > 0
