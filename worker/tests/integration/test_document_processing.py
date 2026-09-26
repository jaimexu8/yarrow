import json
import os
import random
import re
from collections import defaultdict
from typing import ClassVar
from uuid import UUID

import pytest
from sqlalchemy import func, select
from yarrow_db.models import (
    Document,
    Job,
    Page,
    Region,
    RegionTable,
    RegionText,
    Table,
    TableCell,
)
from yarrow_db.session import session_scope

from app.tasks.ingestion import _describe_failed_pages, process_document_task

pytestmark = pytest.mark.integration


def assert_matches_ground_truth(document_id: UUID, model_graph_gt: dict, merged: bool = False):
    """Rebuild the persisted row graph in ground-truth shape and compare it"""

    expected = model_graph_gt["merged" if merged else "unmerged"]

    # Get relevant db objects from the database for the given document_id
    with session_scope() as session:
        documents = session.scalar(select(func.count()).select_from(Document).where(Document.id == document_id))
        pages = session.execute(select(Page.id, Page.page_number).where(Page.document_id == document_id).order_by(Page.page_number)).all()
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
            select(Table.id, Table.row_count, Table.col_count, Table.is_stitched, Table.title).where(Table.document_id == document_id)
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
            ).where(TableCell.region_table_id.in_([table_region.id for table_region in table_regions]))
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
    actual_pages = [{"page_number": page.page_number, "regions": regions_by_page[page.id]} for page in pages]
    expected_pages = [
        {
            "page_number": page_gt["page_number"],
            "regions": [
                {key: region_gt[key] for key in ("reading_order", "region_type", "is_table", "has_text")} for region_gt in page_gt["regions"]
            ],
        }
        for page_gt in model_graph_gt["pages"]
    ]
    assert actual_pages == expected_pages, "pages or regions differ from ground truth"

    # Constructs the mapping from table cell to table region it belongs to
    cells_by_table_region = defaultdict(list)
    for cell in cells:
        cells_by_table_region[cell.region_table_id].append([cell.row_idx, cell.col_idx, cell.row_span, cell.col_span, cell.is_header])

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
            "parts": sorted(parts_by_table[table.id], key=lambda part: part["reading_order"]),
        }
        for table in tables
    ]
    # Tables have no order column; the ground truth lists them in the order of
    # the first region each one hangs off.
    actual_tables.sort(
        key=lambda table: ((table["parts"][0]["page_number"], table["parts"][0]["region_reading_order"]) if table["parts"] else (0, 0))
    )
    expected_tables = [
        {
            "row_count": table["row_count"],
            "col_count": table["col_count"],
            "is_stitched": table["is_stitched"],
            "has_title": table["title"] is not None,
            "parts": [{**part, "cells": sorted(part["cells"])} for part in table["parts"]],
        }
        for table in expected["tables"]
    ]

    assert actual_tables == expected_tables, "tables differ from ground truth"


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
        "unsupported.txt",
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

        with open(os.path.join(TestDocumentProcessing.GROUND_TRUTH_DIR, f"{filename}.json"), "r") as f:
            test_gts.append(json.load(f))

        seeded_documents = test_initializer.configure(filenames=[filename], filepaths=[filepath])

        for gt, seeded_document in zip(test_gts, seeded_documents):
            if gt["kind"] == "unsupported":
                with pytest.raises(ValueError, match=re.escape(gt["expected_error_message"])):
                    process_document_task(job_id=str(seeded_document.job_id), merge_consecutive_tables=merge_consecutive_tables)
            else:
                process_document_task(job_id=str(seeded_document.job_id), merge_consecutive_tables=merge_consecutive_tables)

        for gt, seeded_document in zip(test_gts, seeded_documents):
            with session_scope() as session:
                job = session.get(Job, seeded_document.job_id)
                document = session.get(Document, seeded_document.document_id)

                if gt["kind"] == "unsupported":
                    assert job.status == "failed", gt["file"]
                    assert document.status == "failed", gt["file"]
                    assert job.error_message == gt["expected_error_message"], gt["file"]
                    assert session.scalar(select(func.count()).select_from(Page).where(Page.document_id == seeded_document.document_id)) == 0, gt[
                        "file"
                    ]
                    continue

                assert job.status == "completed", f"{gt['file']}: {job.error_message}"
                assert job.error_message is None, f"{gt['file']}: {job.error_message}"
                assert job.total_pages == gt["expected_pages"], gt["file"]
                assert job.pages_processed == gt["expected_pages"], gt["file"]
                assert document.status == "completed", gt["file"]
                assert document.page_count == gt["expected_pages"], gt["file"]

            assert_matches_ground_truth(seeded_document.document_id, gt["model_graph"], merged=merge_consecutive_tables)

    @pytest.mark.parametrize("filename", filenames)
    @pytest.mark.parametrize("merge_consecutive_tables", merge_consecutive_tables)
    def test_partial_processing(self, test_initializer, parser, filename, merge_consecutive_tables):
        """Runs document processing with simulated failure, reprocesses failed pages, and checks model graph structure against ground truth."""

        filepath = os.path.join(TestDocumentProcessing.TEST_DOCS_DIR, filename)
        test_gts = []

        with open(os.path.join(TestDocumentProcessing.GROUND_TRUTH_DIR, f"{filename}.json"), "r") as f:
            test_gts.append(json.load(f))

        seeded_documents = test_initializer.configure(filenames=[filename], filepaths=[filepath])

        for gt, seeded_document in zip(test_gts, seeded_documents):
            if gt["kind"] == "unsupported":
                with pytest.raises(ValueError, match=re.escape(gt["expected_error_message"])):
                    process_document_task(job_id=str(seeded_document.job_id))

                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == "failed", gt["file"]
                    assert document.status == "failed", gt["file"]
                    assert job.error_message == gt["expected_error_message"], gt["file"]
                    assert session.scalar(select(func.count()).select_from(Page).where(Page.document_id == seeded_document.document_id)) == 0, gt[
                        "file"
                    ]

            else:
                expected_pages = gt["expected_pages"]

                # Configures pages for simulated failure (0-based page indices)
                failed_pages_tuple = tuple(random.sample(range(expected_pages), min(5, expected_pages // 2)))
                parser.configure(failed=failed_pages_tuple)

                # Processes document
                process_document_task(job_id=str(seeded_document.job_id), merge_consecutive_tables=merge_consecutive_tables)

                expected_pages = gt["expected_pages"]
                expected_failed_pages = sorted(index + 1 for index in failed_pages_tuple if index < expected_pages)
                expected_succeeded = expected_pages - len(expected_failed_pages)
                expected_summary = _describe_failed_pages(expected_failed_pages, expected_pages) if expected_failed_pages else None
                expected_status = "completed" if expected_succeeded else "failed"

                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == expected_status, f"{gt['file']}: {job.error_message}"
                    assert job.error_message == expected_summary, gt["file"]
                    assert job.total_pages == expected_pages, gt["file"]
                    assert job.pages_processed == expected_succeeded, gt["file"]
                    assert document.status == expected_status, gt["file"]
                    assert document.page_count == expected_pages, gt["file"]

                # Attempts to reprocess only the failed pages
                parser.configure(failed=())
                process_document_task(
                    job_id=str(seeded_document.job_id), page_to_process=expected_failed_pages, merge_consecutive_tables=merge_consecutive_tables
                )

                # Verifies that all the model objects are updated correctly after reprocessing
                with session_scope() as session:
                    job = session.get(Job, seeded_document.job_id)
                    document = session.get(Document, seeded_document.document_id)

                    assert job.status == "completed", f"{gt['file']}: {job.error_message}"
                    assert job.pages_processed == expected_pages, gt["file"]
                    assert document.status == "completed", gt["file"]
                    assert document.page_count == expected_pages, gt["file"]

                # Verifies all the model objects are updated correctly after reprocessing
                assert_matches_ground_truth(seeded_document.document_id, gt["model_graph"], merged=merge_consecutive_tables)
