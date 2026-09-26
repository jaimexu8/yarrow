"""US-7 (upload), US-8 (bulk), US-37 (reject unsupported types) and the
ownership rules behind the document endpoints."""

import uuid

from yarrow_db.models import Job

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


def _pdf():
    return (
        "files",
        ("sample.pdf", (FIXTURES_DIR / "sample.pdf").read_bytes(), "application/pdf"),
    )


class TestList:
    async def test_requires_auth(self, client):
        assert (await client.get(DOCUMENTS)).status_code == 401

    async def test_new_user_has_empty_library(self, client, auth_headers):
        response = await client.get(DOCUMENTS, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_unknown_document_is_404(self, client, auth_headers):
        response = await client.get(f"{DOCUMENTS}{uuid.uuid4()}", headers=auth_headers)
        assert response.status_code == 404


class TestUpload:
    async def test_valid_pdf_is_accepted_and_queued(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        response = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rejected"] == []
        assert len(body["accepted"]) == 1
        accepted = body["accepted"][0]
        assert accepted["filename"] == "sample.pdf"
        assert accepted["task_id"] == "task-1"
        assert len(fake_storage.objects) == 1
        assert len(fake_queue) == 1

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [d["filename"] for d in library] == ["sample.pdf"]
        assert library[0]["status"] == "queued"

    async def test_upload_persists_celery_task_id_on_job(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """US-42 needs the task id to revoke a queued job."""
        response = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])
        job_id = uuid.UUID(response.json()["accepted"][0]["job_id"])

        job = await db_session.get(Job, job_id)

        assert job is not None
        assert job.celery_task_id == "task-1"
        assert job.status == "queued"

    async def test_unsupported_type_is_rejected_with_reason(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        files = [("files", ("notes.txt", b"just some text", "text/plain"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 400
        [rejected] = response.json()["detail"]
        assert rejected["filename"] == "notes.txt"
        assert "Unsupported file type" in rejected["reason"]
        assert fake_storage.objects == {}
        assert fake_queue == []

    async def test_spoofed_extension_is_still_rejected(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Type comes from the bytes, not the filename or content type."""
        files = [("files", ("fake.pdf", b"this is not a pdf", "application/pdf"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"][0]["reason"]

    async def test_bulk_upload_keeps_valid_files_when_one_fails(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        files = [_pdf(), ("files", ("bad.txt", b"nope", "text/plain"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 200, response.text
        body = response.json()
        assert [a["filename"] for a in body["accepted"]] == ["sample.pdf"]
        assert [r["filename"] for r in body["rejected"]] == ["bad.txt"]
        assert len(fake_queue) == 1

    async def test_requires_auth(self, client):
        response = await client.post(UPLOAD, files=[_pdf()])
        assert response.status_code == 401
