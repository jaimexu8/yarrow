"""Property-based checks for partial page loading.

These target the Phase 5 load_pages/count_pages API, so the module currently
fails on missing attributes. Input files are read once at import time.
"""

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.pipeline.document_parser import DocumentParser

TEST_DOCS = Path(__file__).resolve().parents[1] / "integration" / "test_docs"
PDF_DATA = (TEST_DOCS / "three_page.pdf").read_bytes()
PNG_DATA = (TEST_DOCS / "single_page.png").read_bytes()

TOTAL_PDF_PAGES = 3

PROFILE = settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def page_subsets(draw, total_pages: int):
    """A non-empty subset of 1..total_pages, in arbitrary caller order."""
    selected = draw(
        st.lists(
            st.integers(min_value=1, max_value=total_pages),
            min_size=1,
            max_size=total_pages,
            unique=True,
        )
    )
    return draw(st.permutations(selected))


@given(page_numbers=page_subsets(TOTAL_PDF_PAGES))
@PROFILE
def test_pdf_load_pages_populates_exactly_the_selected_pages(page_numbers):
    parser = DocumentParser()
    parser.load_pages(PDF_DATA, page_numbers)

    assert len(parser.pages) == parser.count_pages(PDF_DATA) == TOTAL_PDF_PAGES
    populated = {index + 1 for index, page in enumerate(parser.pages) if page}
    assert populated == set(page_numbers)


@given(page_numbers=page_subsets(3))
@PROFILE
def test_image_load_pages_only_ever_populates_page_one(page_numbers):
    parser = DocumentParser()
    parser.load_pages(PNG_DATA, page_numbers)

    assert len(parser.pages) == parser.count_pages(PNG_DATA) == 1
    populated = [index + 1 for index, page in enumerate(parser.pages) if page]
    assert populated == ([1] if 1 in page_numbers else [])
