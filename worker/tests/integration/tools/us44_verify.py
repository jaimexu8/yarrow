"""US-44 verification: dump a document's stored reading order and check it"""

import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from sqlalchemy import select
from yarrow_db.models import Document, Page, Region, RegionText
from yarrow_db.session import session_scope

# A block wider than this fraction of the page width is treated as spanning
# (title, full-width table, footer) and exempt from the column check.
SPANNING_FRACTION = 0.6


def find_document(session, key):
    try:
        document = session.scalar(select(Document).where(Document.id == uuid.UUID(key)))
        if document is not None:
            return document
    except ValueError:
        pass
    return session.scalar(
        select(Document)
        .where(Document.filename == key)
        .order_by(Document.created_at.desc())
        .limit(1)
    )


def seed_and_process(filename):
    """Run the real processing task on a test_docs file; rows cleaned up
    by the caller after the check."""
    from conftest import TestInitializer

    from app.tasks.ingestion import process_document_task

    initializer = TestInitializer()
    records = initializer.configure(
        filenames=[filename],
        filepaths=[os.path.join(HERE, "..", "test_docs", filename)],
    )
    process_document_task(job_id=str(records[0].job_id))
    return initializer, records[0].document_id


def main(key):
    initializer = None
    try:
        with session_scope() as session:
            document = find_document(session, key)
            if document is None:
                if os.path.isfile(os.path.join(HERE, "..", "test_docs", key)):
                    print(
                        f"no stored document for {key!r}; processing the test file now"
                    )
                    initializer, document_id = seed_and_process(key)
                else:
                    raise SystemExit(f"no document found for {key!r}")
                with session_scope() as session:
                    document = session.get(Document, document_id)
            pages = (
                session.execute(
                    select(Page)
                    .where(Page.document_id == document.id)
                    .order_by(Page.page_number)
                )
                .scalars()
                .all()
            )
            regions_by_page = {}
            region_ids = []
            for region in (
                session.execute(
                    select(Region).where(
                        Region.page_id.in_([page.id for page in pages])
                    )
                )
                .scalars()
                .all()
            ):
                regions_by_page.setdefault(region.page_id, []).append(region)
                region_ids.append(region.id)
            texts = {
                row.region_id: row.text_content
                for row in session.execute(
                    select(RegionText).where(RegionText.region_id.in_(region_ids))
                )
                .scalars()
                .all()
            }

        violations = 0
        for page in pages:
            width = page.width or 0
            regions = sorted(
                regions_by_page.get(page.id, []), key=lambda r: r.reading_order
            )
            print(f"--- page {page.page_number} (width={width}) ---")
            seen_right = False
            for region in regions:
                text = (texts.get(region.id) or "").replace("\n", " ")
                snippet = text[:80]
                print(
                    f"  [{region.reading_order:>2}] {region.region_type:<18} "
                    f"({region.x0:7.1f},{region.y0:7.1f},{region.x1:7.1f},{region.y1:7.1f}) {snippet}"
                )
                if width and region.x1 - region.x0 < SPANNING_FRACTION * width:
                    center = (region.x0 + region.x1) / 2
                    if center > width / 2:
                        seen_right = True
                    elif seen_right:
                        print(
                            f"    VIOLATION: left-column block after right-column block (region {region.id})"
                        )
                        violations += 1

            page_text = "\n\n".join(
                (texts.get(region.id) or "")
                for region in regions
                if texts.get(region.id)
            )
            print(f"--- page {page.page_number} reading-order text ---")
            print(page_text)
            print()

        if violations:
            print(f"FAIL: {violations} left-after-right violation(s)")
            return 1
        print("PASS: no left-after-right violations")
        return 0
    finally:
        if initializer is not None:
            initializer.teardown()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
