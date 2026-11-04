import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, DocumentShare, Job, JobPage, Page, User

from app.services.failure_messages import PAGE_ERROR_MESSAGES, PAGE_FAILED_MESSAGE
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"
RAW_ERROR = "ReadTimeout: HTTPConnectionPool(host='inference.internal', port=8080)"


def _progress_url(document_id) -> str:
    return f"{DOCUMENTS}{document_id}/progress"


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        data={"allow_duplicate": "true"},
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _start(db_session, job_id, *, total_pages: int, page_count: int | None = None):
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = "processing"
    job.current_stage = "parsing"
    job.total_pages = total_pages
    job.pages_processed = 0
    document.page_count = page_count
    db_session.add_all(
        JobPage(job_id=job_id, page_number=number, status="queued")
        for number in range(1, total_pages + 1)
    )
    await db_session.flush()
    return job


async def _set_page(db_session, job_id, page_number, status, *, code=None, message=None):
    job_page = (
        await db_session.execute(
            select(JobPage).where(
                JobPage.job_id == job_id, JobPage.page_number == page_number
            )
        )
    ).scalar_one()
    job_page.status = status
    job_page.started_at = datetime(2026, 11, 4, 15, 1, 58)
    if status in ("completed", "failed"):
        job_page.finished_at = datetime(2026, 11, 4, 15, 2, 4)
    job_page.error_code = code
    job_page.error_message = message
    await db_session.flush()


async def _progress(client, headers, document_id) -> dict:
    response = await client.get(_progress_url(document_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _other_user(register, verify, login, db_session) -> tuple[uuid.UUID, dict]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()["access_token"]
    user_id = (
        await db_session.execute(select(User.id).where(User.email == payload["email"]))
    ).scalar_one()
    return user_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestPercent:
    async def test_queued_job_is_at_zero(self, client, auth_headers):
        document_id, job_id = await _upload(client, auth_headers)

        progress = await _progress(client, auth_headers, document_id)

        assert progress["document_id"] == str(document_id)
        assert progress["document_status"] == "queued"
        assert progress["job_id"] == str(job_id)
        assert progress["job_status"] == "queued"
        assert progress["current_stage"] == "queued"
        assert progress["pages_total"] == 0
        assert progress["pages_completed"] == 0
        assert progress["pages_failed"] == 0
        assert progress["progress_percent"] == 0
        assert progress["updated_at"] is not None
        assert progress["pages"] == []

    async def test_mid_run_counts_completed_and_failed_pages(
        self, client, auth_headers, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=4)
        await _set_page(db_session, job_id, 1, "completed")
        await _set_page(
            db_session, job_id, 2, "failed", code="inference_timeout", message=RAW_ERROR
        )
        await _set_page(db_session, job_id, 3, "completed")
        await _set_page(db_session, job_id, 4, "processing")

        progress = await _progress(client, auth_headers, document_id)

        assert progress["pages_total"] == 4
        assert progress["pages_completed"] == 2
        assert progress["pages_failed"] == 1
        assert progress["progress_percent"] == 75
        assert [page["status"] for page in progress["pages"]] == [
            "completed",
            "failed",
            "completed",
            "processing",
        ]
        assert progress["pages"][1]["error_code"] == "inference_timeout"
        assert progress["pages"][1]["error_message"] == (
            PAGE_ERROR_MESSAGES["inference_timeout"]
        )
        assert progress["pages"][3]["started_at"] == "2026-11-04T15:01:58"
        assert progress["pages"][3]["finished_at"] is None

    async def test_percent_rounds_down(self, client, auth_headers, db_session):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=3)
        await _set_page(db_session, job_id, 1, "completed")
        await _set_page(db_session, job_id, 2, "completed")

        progress = await _progress(client, auth_headers, document_id)

        assert progress["progress_percent"] == 66

    @pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
    async def test_terminal_job_is_at_hundred(
        self, client, auth_headers, db_session, status
    ):
        document_id, job_id = await _upload(client, auth_headers)
        job = await _start(db_session, job_id, total_pages=4)
        await _set_page(db_session, job_id, 1, "completed")
        job.status = status
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert progress["job_status"] == status
        assert progress["progress_percent"] == 100


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestPageList:
    async def test_active_job_pages_override_stored_pages(
        self, client, auth_headers, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        db_session.add_all(
            [
                Page(document_id=document_id, page_number=1, status="completed"),
                Page(
                    document_id=document_id,
                    page_number=2,
                    status="failed",
                    error_code="inference_unavailable",
                    error_message=RAW_ERROR,
                ),
                Page(document_id=document_id, page_number=3, status="completed"),
            ]
        )
        job = await db_session.get(Job, job_id)
        job.status = "processing"
        job.total_pages = 1
        db_session.add(JobPage(job_id=job_id, page_number=2, status="processing"))
        document = await db_session.get(Document, document_id)
        document.page_count = 3
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert progress["pages_total"] == 1
        assert progress["progress_percent"] == 0
        assert progress["pages"] == [
            {
                "page_number": 1,
                "status": "completed",
                "error_code": None,
                "error_message": None,
                "started_at": None,
                "finished_at": None,
            },
            {
                "page_number": 2,
                "status": "processing",
                "error_code": None,
                "error_message": None,
                "started_at": None,
                "finished_at": None,
            },
            {
                "page_number": 3,
                "status": "completed",
                "error_code": None,
                "error_message": None,
                "started_at": None,
                "finished_at": None,
            },
        ]

    async def test_finished_job_reads_stored_pages(
        self, client, auth_headers, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=2, page_count=2)
        await _set_page(db_session, job_id, 1, "completed")
        await _set_page(db_session, job_id, 2, "completed")
        db_session.add_all(
            [
                Page(document_id=document_id, page_number=1, status="completed"),
                Page(
                    document_id=document_id,
                    page_number=2,
                    status="failed",
                    error_code="invalid_page_image",
                    error_message=RAW_ERROR,
                ),
            ]
        )
        job = await db_session.get(Job, job_id)
        job.status = "completed"
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert [page["status"] for page in progress["pages"]] == ["completed", "failed"]
        assert progress["pages"][1]["error_code"] == "invalid_page_image"
        assert progress["pages"][1]["error_message"] == (
            PAGE_ERROR_MESSAGES["invalid_page_image"]
        )

    async def test_unknown_pages_are_null(self, client, auth_headers, db_session):
        document_id, job_id = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        document.page_count = 2
        job = await db_session.get(Job, job_id)
        job.status = "canceled"
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert [page["status"] for page in progress["pages"]] == [None, None]
        assert [page["page_number"] for page in progress["pages"]] == [1, 2]

    async def test_list_covers_highest_job_page(self, client, auth_headers, db_session):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=2, page_count=1)
        db_session.add(JobPage(job_id=job_id, page_number=5, status="queued"))
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert [page["page_number"] for page in progress["pages"]] == [1, 2, 3, 4, 5]
        assert [page["status"] for page in progress["pages"]] == [
            "queued",
            "queued",
            None,
            None,
            "queued",
        ]

    async def test_latest_job_is_reported(self, client, auth_headers, db_session):
        document_id, first_job_id = await _upload(client, auth_headers)
        first = await db_session.get(Job, first_job_id)
        first.status = "completed"
        first.current_stage = "finished"
        latest = Job(
            document_id=document_id,
            status="queued",
            current_stage="queued",
            pages_processed=0,
            total_pages=2,
            created_at=datetime(2100, 1, 1),
        )
        db_session.add(latest)
        await db_session.flush()

        progress = await _progress(client, auth_headers, document_id)

        assert progress["job_id"] == str(latest.id)
        assert progress["job_status"] == "queued"
        assert progress["pages_total"] == 2
        assert progress["progress_percent"] == 0


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestAccess:
    async def test_shared_reader_can_read(
        self, client, auth_headers, db_session, register, verify, login
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=2)
        reader_id, reader_headers = await _other_user(register, verify, login, db_session)
        db_session.add(
            DocumentShare(
                document_id=document_id, shared_with_user_id=reader_id, permission="view"
            )
        )
        await db_session.flush()

        progress = await _progress(client, reader_headers, document_id)

        assert progress["job_id"] == str(job_id)
        assert progress["pages_total"] == 2

    async def test_stranger_gets_404(
        self, client, auth_headers, db_session, register, verify, login
    ):
        document_id, _ = await _upload(client, auth_headers)
        _, stranger_headers = await _other_user(register, verify, login, db_session)

        response = await client.get(_progress_url(document_id), headers=stranger_headers)

        assert response.status_code == 404
        assert response.json() == {"detail": "Document not found"}

    async def test_missing_document_is_404(self, client, auth_headers):
        response = await client.get(_progress_url(uuid.uuid4()), headers=auth_headers)

        assert response.status_code == 404

    async def test_trashed_document_is_404(self, client, auth_headers, db_session):
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        document.deleted_at = datetime(2026, 11, 4)
        await db_session.flush()

        response = await client.get(_progress_url(document_id), headers=auth_headers)

        assert response.status_code == 404

    async def test_requires_authentication(self, client, auth_headers):
        document_id, _ = await _upload(client, auth_headers)

        response = await client.get(_progress_url(document_id))

        assert response.status_code == 401


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestRawErrorText:
    async def test_raw_error_text_is_never_returned(
        self, client, auth_headers, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _start(db_session, job_id, total_pages=3)
        await _set_page(
            db_session, job_id, 1, "failed", code="inference_error", message=RAW_ERROR
        )
        await _set_page(db_session, job_id, 2, "failed", code="something_new", message=RAW_ERROR)
        await _set_page(db_session, job_id, 3, "failed", code=None, message=RAW_ERROR)

        response = await client.get(_progress_url(document_id), headers=auth_headers)

        assert RAW_ERROR not in response.text
        assert "inference.internal" not in response.text
        assert [page["error_message"] for page in response.json()["pages"]] == [
            PAGE_ERROR_MESSAGES["inference_error"],
            PAGE_FAILED_MESSAGE,
            PAGE_FAILED_MESSAGE,
        ]

    async def test_document_detail_exposes_code_not_raw_text(
        self, client, auth_headers, db_session
    ):
        document_id, _ = await _upload(client, auth_headers)
        db_session.add(
            Page(
                document_id=document_id,
                page_number=1,
                status="failed",
                error_code="inference_unavailable",
                error_message=RAW_ERROR,
            )
        )
        await db_session.flush()

        response = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        assert RAW_ERROR not in response.text
        page = response.json()["pages"][0]
        assert page["error_code"] == "inference_unavailable"
        assert page["error_message"] == PAGE_ERROR_MESSAGES["inference_unavailable"]

    async def test_parsed_tree_exposes_code_not_raw_text(
        self, client, auth_headers, db_session
    ):
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        document.status = "completed"
        document.page_count = 2
        db_session.add_all(
            [
                Page(document_id=document_id, page_number=1, status="completed"),
                Page(
                    document_id=document_id,
                    page_number=2,
                    status="failed",
                    error_code="inference_timeout",
                    error_message=RAW_ERROR,
                ),
            ]
        )
        await db_session.flush()

        response = await client.get(
            f"{DOCUMENTS}{document_id}/parsed", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert RAW_ERROR not in response.text
        pages = response.json()["pages"]
        assert [page["error_code"] for page in pages] == [None, "inference_timeout"]
        assert [page["error_message"] for page in pages] == [
            None,
            PAGE_ERROR_MESSAGES["inference_timeout"],
        ]
