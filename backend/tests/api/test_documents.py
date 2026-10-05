"""US-7 (upload), US-8 (bulk), US-37 (reject unsupported types) and the
ownership rules behind the document endpoints."""

import uuid

from yarrow_db.models import Job

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


WORKER_TEST_DOCS = (
    FIXTURES_DIR.parents[2] / "worker" / "tests" / "integration" / "test_docs"
)


def _worker_doc(name: str) -> bytes:
    """Real sample images shared with the worker's tests."""
    return (WORKER_TEST_DOCS / name).read_bytes()


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

    async def test_only_own_documents(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        library = (await client.get(DOCUMENTS, headers=other)).json()
        assert library == []


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

    async def test_bulk_upload_accepts_each_file_and_lists_all(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """US-8: several valid files in one go, each queued and in the library."""
        files = [
            _pdf(),
            ("files", ("scan.png", _worker_doc("single_page.png"), "image/png")),
            ("files", ("photo.jpg", _worker_doc("single_page.jpg"), "image/jpeg")),
        ]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 200, response.text
        assert len(response.json()["accepted"]) == 3
        assert len(fake_queue) == 3
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert sorted(d["filename"] for d in library) == [
            "photo.jpg",
            "sample.pdf",
            "scan.png",
        ]
        assert {d["status"] for d in library} == {"queued"}


class TestRejectionMessages:
    """US-37: a rejected file comes with a reason a user can act on."""

    async def test_unsupported_type_names_the_accepted_types(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        files = [
            ("files", ("notes.docx", b"PK\x03\x04 word file", "application/msword"))
        ]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        reason = response.json()["detail"][0]["reason"]
        assert "PDF" in reason and "PNG" in reason and "JPEG" in reason

    async def test_oversized_file_states_the_limit_in_megabytes(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024 * 1024)
        big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
        files = [("files", ("big.pdf", big, "application/pdf"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 400
        reason = response.json()["detail"][0]["reason"]
        assert "1 MB" in reason
        assert "byte" not in reason
        assert fake_storage.objects == {}

    async def test_empty_file_is_rejected(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        files = [("files", ("empty.pdf", b"", "application/pdf"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.status_code == 400
        assert response.json()["detail"][0]["reason"] == "File is empty"

    async def test_problems_with_the_file_are_not_retryable(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Retrying can't fix a wrong file type, so no Retry is offered."""
        files = [("files", ("notes.txt", b"just text", "text/plain"))]

        response = await client.post(UPLOAD, headers=auth_headers, files=files)

        assert response.json()["detail"][0]["retryable"] is False

    async def test_storage_outage_is_retryable(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """A storage hiccup is on our side and may clear, so Retry is offered."""

        def unavailable(_file, _key):
            raise ConnectionError("storage down")

        fake_storage.upload_file = unavailable

        response = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])

        [rejected] = response.json()["detail"]
        assert rejected["retryable"] is True
        assert fake_queue == []


class TestRetrySafety:
    """Retrying with the same client_upload_ids never stores a file twice."""

    async def test_retry_with_same_id_returns_the_stored_document(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        size = len((FIXTURES_DIR / "sample.pdf").read_bytes())
        body = {"client_upload_ids": "retry-abc-123"}

        first = await client.post(
            UPLOAD, headers=auth_headers, files=[_pdf()], data=body
        )
        second = await client.post(
            UPLOAD, headers=auth_headers, files=[_pdf()], data=body
        )

        first_doc = first.json()["accepted"][0]["document_id"]
        assert second.status_code == 200
        assert second.json()["accepted"][0]["document_id"] == first_doc
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert len(library) == 1
        assert len(fake_storage.objects) == 1
        assert len(fake_queue) == 1
        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
        assert me["storage_used_bytes"] == size

    async def test_different_ids_are_separate_uploads(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Uploading the same file on purpose twice is allowed."""
        for upload_id in ("one", "two"):
            await client.post(
                UPLOAD,
                headers=auth_headers,
                files=[_pdf()],
                data={"client_upload_ids": upload_id},
            )

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert len(library) == 2

    async def test_ids_are_per_user(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        """Another user's id can't be used to see or claim their document."""
        body = {"client_upload_ids": "shared-id"}
        mine = await client.post(
            UPLOAD, headers=auth_headers, files=[_pdf()], data=body
        )
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        theirs = await client.post(UPLOAD, headers=other, files=[_pdf()], data=body)

        assert (
            theirs.json()["accepted"][0]["document_id"]
            != mine.json()["accepted"][0]["document_id"]
        )

    async def test_one_id_per_file_is_required(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        files = [
            _pdf(),
            ("files", ("scan.png", _worker_doc("single_page.png"), "image/png")),
        ]

        response = await client.post(
            UPLOAD, headers=auth_headers, files=files, data={"client_upload_ids": "a"}
        )

        assert response.status_code == 422
        assert fake_storage.objects == {}


class TestQueueOutage:
    async def test_queue_failure_keeps_the_upload_and_marks_it_failed(
        self, client, auth_headers, fake_storage, monkeypatch
    ):
        """The file is already stored when queueing fails. Reporting an error
        would invite a second upload and a duplicate, so the upload succeeds
        and the document shows as failed with a reason instead."""
        from app.api.v1.endpoints import documents

        def broker_down(_job_id):
            raise ConnectionError("broker unreachable")

        monkeypatch.setattr(documents, "enqueue_document_processing", broker_down)

        response = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])

        assert response.status_code == 200, response.text
        [accepted] = response.json()["accepted"]
        # The response itself says processing didn't start, so the page can
        # tell the user rather than claiming the file is queued.
        assert accepted["status"] == "failed"
        assert "processing could not be started" in accepted["message"]
        [doc] = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert doc["status"] == "failed"
        assert "processing could not be started" in doc["error_message"]
        assert "broker" not in doc["error_message"]

    async def test_one_queue_failure_does_not_affect_other_files(
        self, client, auth_headers, fake_storage, monkeypatch
    ):
        from app.api.v1.endpoints import documents

        calls = []

        def flaky(job_id):
            calls.append(job_id)
            if len(calls) == 1:
                raise ConnectionError("broker hiccup")
            return "task-ok"

        monkeypatch.setattr(documents, "enqueue_document_processing", flaky)
        files = [
            _pdf(),
            ("files", ("scan.png", _worker_doc("single_page.png"), "image/png")),
        ]

        await client.post(UPLOAD, headers=auth_headers, files=files)

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert sorted(d["status"] for d in library) == ["failed", "queued"]


class TestQuota:
    async def test_storage_used_grows_with_each_upload(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        size = len((FIXTURES_DIR / "sample.pdf").read_bytes())
        await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])
        await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])

        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()

        assert me["storage_used_bytes"] == 2 * size

    async def test_upload_over_quota_is_rejected_with_a_reason(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        from app.core.config import settings

        size = len((FIXTURES_DIR / "sample.pdf").read_bytes())
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", size + 10)
        first = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])
        second = await client.post(UPLOAD, headers=auth_headers, files=[_pdf()])

        assert first.status_code == 200
        assert second.status_code == 400
        assert "storage" in second.json()["detail"][0]["reason"].lower()
