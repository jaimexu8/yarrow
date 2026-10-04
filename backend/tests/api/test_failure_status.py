"""US-11: the user can tell when processing has failed, and why.

The worker's outcomes are simulated by writing the rows it would write, so
these run without Celery or the inference server.
"""

import uuid

from yarrow_db.models import Document, Job, Page

from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    PAGE_FAILED_MESSAGE,
)
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, auth_headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=auth_headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _finish(
    db_session,
    document_id,
    job_id,
    *,
    status: str,
    message: str | None,
    pages: list[tuple[str, str | None]] = (),
):
    """Write what the worker writes when a job ends."""
    document = await db_session.get(Document, document_id)
    job = await db_session.get(Job, job_id)
    document.status = job.status = status
    document.error_message = job.error_message = message
    for number, (page_status, page_error) in enumerate(pages, start=1):
        db_session.add(
            Page(
                document_id=document_id,
                page_number=number,
                status=page_status,
                error_message=page_error,
            )
        )
    await db_session.flush()


async def _listed(client, auth_headers, document_id) -> dict:
    library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
    return next(d for d in library if d["id"] == str(document_id))


class TestFailedIsShownAsFailed:
    async def test_failed_document_is_failed_in_list_and_detail(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session,
            document_id,
            job_id,
            status="failed",
            message="3 of 3 page(s) failed: 1, 2, 3",
        )

        listed = await _listed(client, auth_headers, document_id)
        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()

        assert listed["status"] == "failed"
        assert listed["error_message"] == "3 of 3 page(s) failed: 1, 2, 3"
        assert detail["status"] == "failed"
        assert detail["jobs"][0]["status"] == "failed"

    async def test_failed_document_always_has_a_reason(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """A bare "Failed" with no explanation is not enough."""
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, status="failed", message=None)

        listed = await _listed(client, auth_headers, document_id)

        assert listed["error_message"] == DOCUMENT_FAILED_MESSAGE


class TestFailedIsDistinct:
    async def test_each_state_is_reported_as_itself(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        outcomes = {}
        for status in ("processing", "completed", "failed"):
            document_id, job_id = await _upload(client, auth_headers)
            await _finish(db_session, document_id, job_id, status=status, message=None)
            outcomes[status] = document_id

        for status, document_id in outcomes.items():
            listed = await _listed(client, auth_headers, document_id)
            assert listed["status"] == status
            # Only the failed one carries a failure reason.
            assert (listed["error_message"] is not None) == (status == "failed")


class TestSuccessIsNotMarkedFailed:
    async def test_completed_with_some_failed_pages_stays_completed(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """The worker keeps a document "completed" when some pages worked,
        noting which failed. That must not surface as a failure."""
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session,
            document_id,
            job_id,
            status="completed",
            message="1 of 2 page(s) failed: 2",
            pages=[("completed", None), ("failed", "TimeoutError: inference")],
        )

        listed = await _listed(client, auth_headers, document_id)
        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()

        assert listed["status"] == "completed"
        assert listed["error_message"] == "1 of 2 page(s) failed: 2"
        assert [p["status"] for p in detail["pages"]] == ["completed", "failed"]
        assert detail["pages"][0]["error_message"] is None

    async def test_completed_document_has_no_error(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session,
            document_id,
            job_id,
            status="completed",
            message=None,
            pages=[("completed", None)],
        )

        listed = await _listed(client, auth_headers, document_id)

        assert listed["status"] == "completed"
        assert listed["error_message"] is None


class TestInternalsAreNotShown:
    """The worker can store raw exception text; users never see it (NFR-6)."""

    RAW = (
        "ConnectionError: HTTPConnectionPool(host='inference', port=8080): "
        "Max retries exceeded"
    )

    async def test_raw_document_error_is_replaced(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session, document_id, job_id, status="failed", message=self.RAW
        )

        listed = await _listed(client, auth_headers, document_id)
        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).text

        assert listed["error_message"] == DOCUMENT_FAILED_MESSAGE
        assert "inference" not in detail
        assert "HTTPConnectionPool" not in detail

    async def test_storage_key_is_not_shown(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        message = (
            f"Uploaded file not found in storage: documents/{document_id}/original"
        )
        await _finish(db_session, document_id, job_id, status="failed", message=message)

        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).text

        assert "documents/" not in detail
        assert DOCUMENT_FAILED_MESSAGE in detail

    async def test_raw_page_error_is_replaced(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session,
            document_id,
            job_id,
            status="failed",
            message="1 of 1 page(s) failed: 1",
            pages=[("failed", "ValueError: bad tensor shape (3, 224)")],
        )

        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()

        assert detail["pages"][0]["error_message"] == PAGE_FAILED_MESSAGE
