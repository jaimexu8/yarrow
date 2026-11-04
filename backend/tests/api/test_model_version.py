"""Model name and version on document details and the parsed tree (US-85)"""

import uuid
from datetime import datetime

from yarrow_db.models import Document, Job, Page

from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
DOCUMENTS = "/api/v1/documents/"
NAME = "PP-StructureV3+PaddleOCR-VL"
CURRENT = "3.2.0+vl-0.9b"
PREVIOUS = "3.1.4+vl-0.9b"


async def _upload(client, headers) -> uuid.UUID:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        data={"allow_duplicate": "true"},
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


def _page(document_id, number, status="completed", version=CURRENT):
    completed = status == "completed"
    return Page(
        id=uuid.uuid4(),
        document_id=document_id,
        page_number=number,
        status=status,
        model_name=NAME if completed else None,
        model_version=version if completed else None,
    )


async def test_details_report_document_job_and_grouped_page_models(
    client, auth_headers, db_session, fake_storage, fake_queue
):
    document_id = await _upload(client, auth_headers)
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.model_name = NAME
    document.model_version = CURRENT
    document.pipeline_version = "0.1.0"
    document.processed_at = datetime(2026, 11, 4, 15, 2, 20)
    db_session.add(
        Job(
            id=uuid.uuid4(),
            document_id=document_id,
            status="completed",
            model_name=NAME,
            model_version=CURRENT,
            pipeline_version="0.1.0",
        )
    )
    db_session.add_all(
        [
            _page(document_id, 1, version=PREVIOUS),
            _page(document_id, 2),
            _page(document_id, 3),
            _page(document_id, 4, status="failed"),
        ]
    )
    await db_session.commit()

    response = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == NAME
    assert body["model_version"] == CURRENT
    assert body["pipeline_version"] == "0.1.0"
    assert body["processed_at"] == "2026-11-04T15:02:20"
    assert body["models"] == [
        {"model_name": NAME, "model_version": CURRENT, "page_count": 2},
        {"model_name": NAME, "model_version": PREVIOUS, "page_count": 1},
    ]
    job = next(job for job in body["jobs"] if job["status"] == "completed")
    assert (job["model_name"], job["model_version"], job["pipeline_version"]) == (
        NAME,
        CURRENT,
        "0.1.0",
    )
    pages = {page["page_number"]: page for page in body["pages"]}
    assert (pages[1]["model_name"], pages[1]["model_version"]) == (NAME, PREVIOUS)
    assert (pages[4]["model_name"], pages[4]["model_version"]) == (None, None)


async def test_parsed_tree_carries_document_and_page_models(
    client, auth_headers, db_session, fake_storage, fake_queue
):
    document_id = await _upload(client, auth_headers)
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.model_name = NAME
    document.model_version = CURRENT
    document.pipeline_version = "0.1.0"
    document.processed_at = datetime(2026, 11, 4, 15, 2, 20)
    db_session.add(_page(document_id, 1))
    await db_session.commit()

    response = await client.get(f"{DOCUMENTS}{document_id}/parsed", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["model_name"] == NAME
    assert body["document"]["model_version"] == CURRENT
    assert body["document"]["pipeline_version"] == "0.1.0"
    assert body["document"]["processed_at"] == "2026-11-04T15:02:20"
    assert (body["pages"][0]["model_name"], body["pages"][0]["model_version"]) == (
        NAME,
        CURRENT,
    )


async def test_unprocessed_document_has_no_recorded_model(
    client, auth_headers, fake_storage, fake_queue
):
    document_id = await _upload(client, auth_headers)

    response = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)

    body = response.json()
    assert body["model_name"] is None
    assert body["model_version"] is None
    assert body["pipeline_version"] is None
    assert body["processed_at"] is None
    assert body["models"] == []
