import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Job, Page

from app.core.config import settings
from app.services.failure_messages import INTERRUPTED_MESSAGE
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"

LONG_AGO = datetime.now(UTC).replace(tzinfo=None) - timedelta(
    seconds=settings.JOB_INTERRUPTED_AFTER_SECONDS + 60
)


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _finish(
    db_session,
    document_id,
    job_id,
    page_states: dict[int, str],
    document_status: str = "completed",
) -> None:
    """What the worker leaves behind after a run: page rows and statuses"""
    document = await db_session.get(Document, document_id)
    job = await db_session.get(Job, job_id)
    document.page_count = len(page_states)
    document.status = document_status
    job.status = document_status
    for number, state in page_states.items():
        db_session.add(Page(document_id=document_id, page_number=number, status=state))
    await db_session.flush()


async def _set_status(db_session, document_id, job_id, status, updated_at=None):
    document = await db_session.get(Document, document_id)
    job = await db_session.get(Job, job_id)
    document.status = job.status = status
    if updated_at is not None:
        job.updated_at = updated_at
    await db_session.flush()


async def _reprocess(client, headers, document_id):
    return await client.post(f"{DOCUMENTS}{document_id}/reprocess", headers=headers)


async def _row(client, headers, document_id) -> dict:
    library = (await client.get(DOCUMENTS, headers=headers)).json()
    return next(doc for doc in library if doc["id"] == str(document_id))


async def _completed_pages(db_session, document_id) -> list[int]:
    return list(
        await db_session.scalars(
            select(Page.page_number)
            .where(Page.document_id == document_id, Page.status == "completed")
            .order_by(Page.page_number)
        )
    )


class TestFailedPagesAreReprocessed:
    async def test_library_lists_the_unfinished_pages(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session, document_id, job_id, {1: "completed", 2: "failed", 3: "failed"}
        )

        row = await _row(client, auth_headers, document_id)

        assert row["reprocess"] == {
            "scope": "incomplete",
            "interrupted": False,
            "pages": 2,
            "page_numbers": [2, 3],
        }

    async def test_only_unfinished_pages_are_queued(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(
            db_session,
            document_id,
            job_id,
            {1: "completed", 2: "failed", 3: "completed"},
        )

        response = await _reprocess(client, auth_headers, document_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["error_message"] is None
        
        # A job is running now, so reprocessing is not offered
        assert body["reprocess"] is None
        
        new_job_id = fake_queue[-1]
        assert fake_queue.pages[new_job_id] == [2]
        
        new_job = await db_session.get(Job, new_job_id)
        assert new_job.status == "queued"
        assert new_job.celery_task_id == f"task-{len(fake_queue)}"
        
        # Completed pages and their data are kept
        assert await _completed_pages(db_session, document_id) == [1, 3]

    async def test_failed_document_without_pages_is_queued_whole(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, document_id, job_id, "failed")

        row = await _row(client, auth_headers, document_id)
        response = await _reprocess(client, auth_headers, document_id)

        assert row["reprocess"]["scope"] == "incomplete"
        assert row["reprocess"]["pages"] is None
        assert response.status_code == 200, response.text
        assert fake_queue.pages[fake_queue[-1]] is None


class TestInterruptedRunIsResumed:
    async def test_lost_job_is_flagged(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, document_id, job_id, "processing", LONG_AGO)

        row = await _row(client, auth_headers, document_id)

        assert row["status"] == "processing"
        assert row["reprocess"]["scope"] == "incomplete"
        assert row["reprocess"]["interrupted"] is True

    async def test_reprocessing_retires_the_lost_job(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, document_id, job_id, "processing", LONG_AGO)

        response = await _reprocess(client, auth_headers, document_id)

        assert response.status_code == 200, response.text
        
        job = await db_session.get(Job, job_id)
        await db_session.refresh(job)
        assert job.status == "failed"
        assert job.error_message == INTERRUPTED_MESSAGE
        assert fake_queue[-1] != job_id

    async def test_running_job_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, document_id, job_id, "processing")
        queued_before = len(fake_queue)

        row = await _row(client, auth_headers, document_id)
        response = await _reprocess(client, auth_headers, document_id)

        assert row["reprocess"] is None
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ALREADY_PROCESSING"
        assert len(fake_queue) == queued_before


class TestFinishedDocumentIsReprocessedInFull:
    async def test_library_offers_a_full_reprocess(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "completed", 2: "completed"})

        row = await _row(client, auth_headers, document_id)

        assert row["reprocess"] == {
            "scope": "all",
            "interrupted": False,
            "pages": 2,
            "page_numbers": [],
        }

    async def test_whole_document_is_queued(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "completed", 2: "completed"})

        response = await _reprocess(client, auth_headers, document_id)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        new_job_id = fake_queue[-1]
        assert new_job_id != job_id
        assert fake_queue.pages[new_job_id] is None
        assert (await db_session.get(Job, new_job_id)).total_pages == 2
        # The old results stay until the new run replaces them.
        assert await _completed_pages(db_session, document_id) == [1, 2]

    async def test_canceled_document_is_processed_in_full(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, document_id, job_id, "canceled")

        response = await _reprocess(client, auth_headers, document_id)

        assert response.status_code == 200, response.text
        assert fake_queue.pages[fake_queue[-1]] is None


class TestQueueOutage:
    async def test_document_is_left_as_it_was(
        self, client, auth_headers, fake_storage, fake_queue, db_session, monkeypatch
    ):
        from app.api.v1.endpoints import documents

        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "completed", 2: "failed"})
        document = await db_session.get(Document, document_id)
        document.error_message = "1 of 2 page(s) failed: 2"
        await db_session.flush()

        def _broker_down(job_id, pages=None):
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(documents, "enqueue_document_processing", _broker_down)

        response = await _reprocess(client, auth_headers, document_id)

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"
        row = await _row(client, auth_headers, document_id)
        assert row["status"] == "completed"
        assert row["error_message"] == "1 of 2 page(s) failed: 2"
        # Still offered, so the user can try again.
        assert row["reprocess"]["scope"] == "incomplete"


class TestReprocessInfoEverywhere:
    async def test_rename_keeps_the_reprocess_info(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """The table swaps in the renamed document; Reprocess must survive."""
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "failed"}, "failed")

        response = await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": "renamed.pdf"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["reprocess"]["scope"] == "incomplete"

    @pytest.mark.parametrize("path", ["", "/parsed"])
    async def test_detail_and_viewer_tree_carry_it(
        self, client, auth_headers, fake_storage, fake_queue, db_session, path
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "completed", 2: "failed"})

        body = (
            await client.get(f"{DOCUMENTS}{document_id}{path}", headers=auth_headers)
        ).json()

        document = body["document"] if path else body
        assert document["reprocess"]["page_numbers"] == [2]


class TestAccess:
    async def test_another_users_document_is_not_found(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
        register,
        verify,
        login,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "failed"}, "failed")
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await _reprocess(client, other, document_id)

        assert response.status_code == 404

    async def test_requires_sign_in(self, client):
        response = await client.post(f"{DOCUMENTS}{uuid.uuid4()}/reprocess")
        assert response.status_code == 401
