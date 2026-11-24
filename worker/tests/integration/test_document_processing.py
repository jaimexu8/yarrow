import json
import os
import random
import re
from collections import defaultdict
from typing import ClassVar
from unittest import mock
from uuid import UUID

import pytest
from sqlalchemy import func, select
from yarrow_db.models import (
    Document,
    Job,
    JobPage,
    Page,
    Region,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    Warning,
)
from yarrow_db.session import session_scope

from app.tasks import ingestion
from app.tasks.ingestion import (
    _describe_failed_pages,
    finalize_document,
    ingest_document,
    parse_page,
)

pytestmark = pytest.mark.integration


def assert_matches_ground_truth(
    document_id: UUID, model_graph_gt: dict, merged: bool = False
):
    """Rebuild the persisted row graph in ground-truth shape and compare it"""

    expected = model_graph_gt["merged" if merged else "unmerged"]

    # Get relevant db objects from the database for the given document_id
    with session_scope() as session:
        documents = session.scalar(
            select(func.count()).select_from(Document).where(Document.id == document_id)
        )
        pages = session.execute(
            select(Page.id, Page.page_number)
            .where(Page.document_id == document_id)
            .order_by(Page.page_number)
        ).all()
        regions = session.execute(
            select(Region.id, Region.page_id, Region.reading_order, Region.region_type)
            .join(Page, Region.page_id == Page.id)
            .where(Page.document_id == document_id)
            .order_by(Page.page_number, Region.reading_order)
        ).all()
        text_region_ids = session.scalars(
            select(RegionText.region_id)
            .join(Region, RegionText.region_id == Region.id)
            .join(Page, Region.page_id == Page.id)
            .where(Page.document_id == document_id)
        ).all()
        tables = session.execute(
            select(
                Table.id,
                Table.row_count,
                Table.col_count,
                Table.is_stitched,
                Table.title,
            ).where(Table.document_id == document_id)
        ).all()
        table_regions = session.execute(
            select(
                RegionTable.id,
                RegionTable.table_id,
                RegionTable.region_id,
                RegionTable.reading_order,
                RegionTable.row_start,
                RegionTable.row_end,
                RegionTable.col_start,
                RegionTable.col_end,
                Page.page_number,
                Region.reading_order.label("region_reading_order"),
            )
            .join(Region, RegionTable.region_id == Region.id)
            .join(Page, Region.page_id == Page.id)
            .join(Table, RegionTable.table_id == Table.id)
            .where(Page.document_id == document_id, Table.document_id == document_id)
        ).all()
        cells = session.execute(
            select(
                TableCell.region_table_id,
                TableCell.row_idx,
                TableCell.col_idx,
                TableCell.row_span,
                TableCell.col_span,
                TableCell.is_header,
            ).where(
                TableCell.region_table_id.in_(
                    [table_region.id for table_region in table_regions]
                )
            )
        ).all()

    # Checks if the actual counts match the expected counts
    actual_counts = {
        "documents": documents,
        "pages": len(pages),
        "regions": len(regions),
        "region_texts": len(text_region_ids),
        "tables": len(tables),
        "region_tables": len(table_regions),
        "table_cells": len(cells),
    }

    assert actual_counts == expected["counts"], "row counts differ from ground truth"

    # Constructs the mapping from region to the page it belongs to
    table_region_ids = {table_region.region_id for table_region in table_regions}
    regions_by_page = defaultdict(list)
    for region in regions:
        regions_by_page[region.page_id].append(
            {
                "reading_order": region.reading_order,
                "region_type": region.region_type,
                "is_table": region.id in table_region_ids,
                "has_text": region.id in set(text_region_ids),
            }
        )

    # Constructs the page structures for comparison
    actual_pages = [
        {"page_number": page.page_number, "regions": regions_by_page[page.id]}
        for page in pages
    ]
    expected_pages = [
        {
            "page_number": page_gt["page_number"],
            "regions": [
                {
                    key: region_gt[key]
                    for key in ("reading_order", "region_type", "is_table", "has_text")
                }
                for region_gt in page_gt["regions"]
            ],
        }
        for page_gt in model_graph_gt["pages"]
    ]
    assert actual_pages == expected_pages, "pages or regions differ from ground truth"

    # Constructs the mapping from table cell to table region it belongs to
    cells_by_table_region = defaultdict(list)
    for cell in cells:
        cells_by_table_region[cell.region_table_id].append(
            [cell.row_idx, cell.col_idx, cell.row_span, cell.col_span, cell.is_header]
        )

    # Constructs the mapping from table region to table it belongs to
    parts_by_table = defaultdict(list)
    for table_region in table_regions:
        parts_by_table[table_region.table_id].append(
            {
                "reading_order": table_region.reading_order,
                "page_number": table_region.page_number,
                "region_reading_order": table_region.region_reading_order,
                "row_start": table_region.row_start,
                "row_end": table_region.row_end,
                "col_start": table_region.col_start,
                "col_end": table_region.col_end,
                "cells": sorted(cells_by_table_region[table_region.id]),
            }
        )

    actual_tables = [
        {
            "row_count": table.row_count,
            "col_count": table.col_count,
            "is_stitched": table.is_stitched,
            "has_title": table.title is not None,
            "parts": sorted(
                parts_by_table[table.id], key=lambda part: part["reading_order"]
            ),
        }
        for table in tables
    ]
    # Tables have no order column; the ground truth lists them in the order of
    # the first region each one hangs off.
    actual_tables.sort(
        key=lambda table: (
            (
                table["parts"][0]["page_number"],
                table["parts"][0]["region_reading_order"],
            )
            if table["parts"]
            else (0, 0)
        )
    )
    expected_tables = [
        {
            "row_count": table["row_count"],
            "col_count": table["col_count"],
            "is_stitched": table["is_stitched"],
            "has_title": table["title"] is not None,
            "parts": [
                {**part, "cells": sorted(part["cells"])} for part in table["parts"]
            ],
        }
        for table in expected["tables"]
    ]

    assert actual_tables == expected_tables, "tables differ from ground truth"


def _run_pipeline(
    job_id: UUID, pages: list[int] | None = None, merge: bool = False
) -> None:
    """Drive the four-step cascade in-process for one run.

    `ingest_document` fans out prepare_pages batches, which the harness runs
    to upload rasters and seed queued job pages. Then `parse_page` runs once
    per queued page and `finalize_document` stitches tables and sets status.

    `pages` is the 1-based page list to (re)process; `merge` is the table
    merge flag. The FakeDocumentParser failure simulation is unchanged.
    """
    enqueued: list[tuple[str, list, str]] = []
    with mock.patch.object(
        ingestion.celery_app,
        "send_task",
        side_effect=lambda name, args, queue: enqueued.append((name, args, queue)),
    ):
        ingest_document(str(job_id), pages=pages, merge_consecutive_tables=merge)

        for name, args, _queue in enqueued:
            if name == "app.tasks.ingestion.prepare_pages" and args[0] == str(job_id):
                ingestion.prepare_pages.apply(
                    kwargs={"job_id": str(job_id), "page_numbers": args[1]}
                )

        with session_scope() as session:
            run_page_numbers = list(
                session.execute(
                    select(JobPage.page_number)
                    .where(JobPage.job_id == job_id, JobPage.status == "queued")
                    .order_by(JobPage.page_number)
                ).scalars()
            )

        # The FakeDocumentParser (the `parser` fixture, active only in
        # test_partial_processing) simulates a per-page failure keyed on the
        # parser's internal page_index. parse_page runs a 1-page parser whose
        # index is always 0, so without help the fake would fail every page
        # (when 0 is in the configured set) or none (when it isn't), never
        # the configured subset. Re-point slot 0 to this run page's own number
        # before each parse so exactly the configured pages fail, matching the
        # old monolith test's intent. When the real parser is in use (no
        # `.failed` to re-point) this is a no-op.
        fake = ingestion.DocumentParser
        original_failed = set(fake.failed) if hasattr(fake, "failed") else None

        for page_no in run_page_numbers:
            if original_failed is not None:
                fake.failed = {0} if (page_no - 1) in original_failed else set()
            parse_page.apply(kwargs={"job_id": str(job_id), "page_no": page_no})

        if original_failed is not None:
            fake.failed = original_failed

        finalize_document.apply(kwargs={"job_id": str(job_id)})


class TestDocumentProcessing:
    """Integration tests for document processing."""

    filenames: ClassVar[list[str]] = [
        "sample_short.pdf",
        "sample.pdf",
        "one_page.pdf",
        "single_page.png",
        "single_page.jpg",
        "three_frame.gif",
        "three_page.pdf",
        "sample_multipage_table.pdf",
        "unsupported.txt",
        "two_column.pdf",
    ]
    merge_consecutive_tables: ClassVar[list[bool]] = [
        True,
        False,
    ]

    HERE = os.path.dirname(os.path.abspath(__file__))
    TEST_DOCS_DIR = os.path.join(HERE, "test_docs")
    GROUND_TRUTH_DIR = os.path.join(HERE, "ground_truth")

    @pytest.mark.parametrize("filename", filenames)
    @pytest.mark.parametrize("merge_consecutive_tables", merge_consecutive_tables)
    def test_structure(self, test_initializer, filename, merge_consecutive_tables):
        """Runs document processing on all pages and check model graph structure against the ground truth"""

        filepath = os.path.join(TestDocumentProcessing.TEST_DOCS_DIR, filename)
        test_gts = []

        with open(
            os.path.join(TestDocumentProcessing.GROUND_TRUTH_DIR, f"{filename}.json"),
            "r",
        ) as f:
            test_gts.append(json.load(f))

        seeded_documents = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        for gt, seeded_document in zip(test_gts, seeded_documents):
            if gt["kind"] == "unsupported":
                with pytest.raises(
                    ValueError, match=re.escape(gt["expected_error_message"])
                ):
                    _run_pipeline(
                        job_id=seeded_document.job_id, merge=merge_consecutive_tables
                    )
            else:
                _run_pipeline(
                    job_id=seeded_document.job_id, merge=merge_consecutive_tables
                )

        for gt, seeded_document in zip(test_gts, seeded_documents):
            with session_scope() as session:
                job = session.get(Job, seeded_document.job_id)
                document = session.get(Document, seeded_document.document_id)

                if gt["kind"] == "unsupported":
                    assert job.status == "failed", gt["file"]
                    assert document.status == "failed", gt["file"]
                    assert job.error_message == gt["expected_error_message"], gt["file"]
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(Page)
                            .where(Page.document_id == seeded_document.document_id)
                        )
                        == 0
                    ), gt["file"]
                    continue

                assert job.status == "completed", f"{gt['file']}: {job.error_message}"
                assert job.error_message is None, f"{gt['file']}: {job.error_message}"
                assert job.total_pages == gt["expected_pages"], gt["file"]
                assert job.pages_processed == gt["expected_pages"], gt["file"]
                assert document.status == "completed", gt["file"]
                assert document.page_count == gt["expected_pages"], gt["file"]

            assert_matches_ground_truth(
                seeded_document.document_id,
                gt["model_graph"],
                merged=merge_consecutive_tables,
            )

    @pytest.mark.parametrize("filename", filenames)
    @pytest.mark.parametrize("merge_consecutive_tables", merge_consecutive_tables)
    def test_partial_processing(
        self, test_initializer, parser, filename, merge_consecutive_tables
    ):
        """Runs document processing with simulated failure, reprocesses failed pages, and checks model graph structure against ground truth."""

        filepath = os.path.join(TestDocumentProcessing.TEST_DOCS_DIR, filename)
        test_gts = []

        with open(
            os.path.join(TestDocumentProcessing.GROUND_TRUTH_DIR, f"{filename}.json"),
            "r",
        ) as f:
            test_gts.append(json.load(f))

        seeded_documents = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        for gt, seeded_document in zip(test_gts, seeded_documents):
            if gt["kind"] == "unsupported":
                with pytest.raises(
                    ValueError, match=re.escape(gt["expected_error_message"])
                ):
                    _run_pipeline(job_id=seeded_document.job_id)

                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == "failed", gt["file"]
                    assert document.status == "failed", gt["file"]
                    assert job.error_message == gt["expected_error_message"], gt["file"]
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(Page)
                            .where(Page.document_id == seeded_document.document_id)
                        )
                        == 0
                    ), gt["file"]

            else:
                expected_pages = gt["expected_pages"]

                # Configures pages for simulated failure (0-based page indices)
                failed_pages_tuple = tuple(
                    random.sample(range(expected_pages), min(5, expected_pages // 2))
                )
                parser.configure(failed=failed_pages_tuple)

                # Processes document
                _run_pipeline(
                    job_id=seeded_document.job_id, merge=merge_consecutive_tables
                )

                expected_pages = gt["expected_pages"]
                expected_failed_pages = sorted(
                    index + 1 for index in failed_pages_tuple if index < expected_pages
                )
                expected_succeeded = expected_pages - len(expected_failed_pages)
                expected_summary = (
                    _describe_failed_pages(expected_failed_pages, expected_pages)
                    if expected_failed_pages
                    else None
                )
                expected_status = "completed" if expected_succeeded else "failed"

                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == expected_status, (
                        f"{gt['file']}: {job.error_message}"
                    )
                    assert job.error_message == expected_summary, gt["file"]
                    assert job.total_pages == expected_pages, gt["file"]
                    assert job.pages_processed == expected_pages, gt["file"]
                    assert document.status == expected_status, gt["file"]
                    assert document.page_count == expected_pages, gt["file"]

                # Attempts to reprocess only the failed pages
                parser.configure(failed=())
                _run_pipeline(
                    job_id=seeded_document.job_id,
                    pages=expected_failed_pages,
                    merge=merge_consecutive_tables,
                )

                # Verifies that all the model objects are updated correctly after reprocessing
                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == "completed", (
                        f"{gt['file']}: {job.error_message}"
                    )
                    assert job.total_pages == len(expected_failed_pages), gt["file"]
                    assert job.pages_processed == len(expected_failed_pages), gt["file"]
                    assert document.status == "completed", gt["file"]
                    assert document.page_count == expected_pages, gt["file"]

                # Verifies all the model objects are updated correctly after reprocessing
                assert_matches_ground_truth(
                    seeded_document.document_id,
                    gt["model_graph"],
                    merged=merge_consecutive_tables,
                )


class TestQualityChecker:
    """Integration tests for the per-page quality warnings."""

    expected_warnings: ClassVar[dict[str, set[str]]] = {
        "scan_normal.png": set(),
        # A tilted page is now corrected before OCR, so it emits a rotation
        # warning instead of a skew one.
        "scan_skewed.png": {"Page was auto-rotated"},
        "scan_blurry.png": {"Image is blurry"},
        "scan_truncated.png": {"Image is truncated"},
        "scan_dark.png": {"Image is too dark"},
        "scan_bright.png": {"Image is too bright"},
    }
    filenames: ClassVar[list[str]] = list(expected_warnings)

    HERE = os.path.dirname(os.path.abspath(__file__))
    TEST_DOCS_DIR = os.path.join(HERE, "test_docs")

    @pytest.mark.parametrize("filename", filenames)
    def test_warnings(self, test_initializer, filename):
        """Processes a scan and checks the warnings stored for its page"""

        filepath = os.path.join(TestQualityChecker.TEST_DOCS_DIR, filename)
        (seeded_document,) = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        _run_pipeline(job_id=seeded_document.job_id)

        with session_scope() as session:
            messages = session.scalars(
                select(Warning.message)
                .join(Page, Warning.page_id == Page.id)
                .where(Page.document_id == seeded_document.document_id)
            ).all()

        # The skew message carries the measured angle ("Image is skewed by -18.4°"), so compare on what precedes it
        kinds = {re.sub(r" by .*$", "", message) for message in messages}
        expected = TestQualityChecker.expected_warnings[filename]

        if expected:
            assert expected <= kinds, (
                f"{filename}: expected {sorted(expected)}, got {sorted(kinds)}"
            )
        else:
            assert kinds == set(), (
                f"{filename}: expected no warnings, got {sorted(kinds)}"
            )


class TestRotationCorrection:
    """End-to-end rotation correction: rotated pages are re-stored and warned about."""

    HERE = os.path.dirname(os.path.abspath(__file__))
    TEST_DOCS_DIR = os.path.join(HERE, "test_docs")

    def test_rotated_pages_are_corrected_and_stored(self, test_initializer):
        """Each rotated page of the multi-orientation PDF gets a rotation warning and a corrected image."""

        filename = "ocr_rotation_test.pdf"
        filepath = os.path.join(TestRotationCorrection.TEST_DOCS_DIR, filename)
        (seeded_document,) = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        # Runs the real parser against the live inference gateway; skipped unless the
        # OCR cluster is up (see the module-level integration marker).
        _run_pipeline(job_id=seeded_document.job_id)

        with session_scope() as session:
            pages = session.scalars(
                select(Page)
                .where(Page.document_id == seeded_document.document_id)
                .order_by(Page.page_number)
            ).all()
            rotation_warnings = session.scalars(
                select(Warning.message)
                .join(Page, Warning.page_id == Page.id)
                .where(
                    Page.document_id == seeded_document.document_id,
                    Warning.message.like("Page was auto-rotated%"),
                )
            ).all()

        # Every page of this document is rotated, so every page is corrected and re-stored.
        assert len(pages) == 7, f"expected 7 pages, got {len(pages)}"
        for page in pages:
            assert page.page_image_key is not None, (
                f"page {page.page_number} was not re-stored"
            )
        assert len(rotation_warnings) == 7, (
            f"expected 7 rotation warnings, got {len(rotation_warnings)}"
        )

    def test_upright_scan_is_not_rotated(self, test_initializer):
        """An upright scan gets no rotation warning and no corrected page image."""

        filename = "scan_normal.png"
        filepath = os.path.join(TestRotationCorrection.TEST_DOCS_DIR, filename)
        (seeded_document,) = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        _run_pipeline(job_id=seeded_document.job_id)

        with session_scope() as session:
            (page,) = session.scalars(
                select(Page).where(Page.document_id == seeded_document.document_id)
            ).all()
            rotation_warnings = session.scalars(
                select(Warning.message)
                .join(Page, Warning.page_id == Page.id)
                .where(
                    Page.document_id == seeded_document.document_id,
                    Warning.message.like("Page was auto-rotated%"),
                )
            ).all()

        assert rotation_warnings == [], (
            f"expected no rotation warnings, got {rotation_warnings}"
        )
        assert page.page_image_key is None, "upright page should not be re-stored"


class TestTextLayerPath:
    def test_pdf_text_layer_page_is_model_free_with_embedded_text(
        self, test_initializer, monkeypatch
    ):
        """A PDF page with a usable text layer skips OCR for its text content."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "TEXT_LAYER_ENABLED", True)
        filename = "three_page.pdf"
        filepath = os.path.join(TestDocumentProcessing.TEST_DOCS_DIR, filename)
        (seeded_document,) = test_initializer.configure(
            filenames=[filename], filepaths=[filepath]
        )

        _run_pipeline(job_id=seeded_document.job_id, pages=[1])

        with session_scope() as session:
            page = session.scalar(
                select(Page).where(
                    Page.document_id == seeded_document.document_id,
                    Page.page_number == 1,
                )
            )
            contents = session.scalars(
                select(RegionText.text_content)
                .join(Region, RegionText.region_id == Region.id)
                .where(Region.page_id == page.id)
            ).all()

        assert page.status == "completed"
        assert page.model_name == "model-free"
        assert page.model_version == "model-free"
        assert contents, "expected RegionText rows filled from the embedded words"
        assert any(content.strip() for content in contents)
