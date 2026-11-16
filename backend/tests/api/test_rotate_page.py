import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Job, Page

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _finish(db_session, document_id, job_id, page_states):
    document = await db_session.get(Document, document_id)
    job = await db_session.get(Job, job_id)
    document.page_count = len(page_states)
    document.status = "completed"
    job.status = "completed"
    for number in page_states:
        db_session.add(Page(document_id=document_id, page_number=number, status="completed"))
    await db_session.flush()


async def _set_running(db_session, document_id, job_id):
    document = await db_session.get(Document, document_id)
    job = await db_session.get(Job, job_id)
    document.status = job.status = "processing"
    await db_session.flush()


async def _rotate(client, headers, document_id, page_number, degrees):
    return await client.post(
        f"{DOCUMENTS}{document_id}/pages/{page_number}/rotate",
        headers=headers,
        json={"degrees": degrees},
    )


class TestRotatePage:
    async def test_queues_a_single_page_job_and_stores_the_override(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1, 2, 3})

        response = await _rotate(client, auth_headers, document_id, 2, 90)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        new_job_id = fake_queue[-1]
        assert fake_queue.pages[new_job_id] == [2]
        page = await db_session.scalar(
            select(Page).where(Page.document_id == document_id, Page.page_number == 2)
        )
        assert page.manual_rotation_deg == 90

    async def test_reset_clears_the_override(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1, 2})
        page = await db_session.scalar(
            select(Page).where(Page.document_id == document_id, Page.page_number == 1)
        )
        page.manual_rotation_deg = 180
        await db_session.flush()

        response = await _rotate(client, auth_headers, document_id, 1, None)

        assert response.status_code == 200, response.text
        assert fake_queue.pages[fake_queue[-1]] == [1]
        await db_session.refresh(page)
        assert page.manual_rotation_deg is None

    @pytest.mark.parametrize("degrees", [45, -90, 360])
    async def test_bad_rotation_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, db_session, degrees
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1})

        queued_before = len(fake_queue)
        response = await _rotate(client, auth_headers, document_id, 1, degrees)

        assert response.status_code == 409
        assert len(fake_queue) == queued_before

    async def test_missing_page_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1})

        queued_before = len(fake_queue)
        response = await _rotate(client, auth_headers, document_id, 5, 90)

        assert response.status_code == 409
        assert len(fake_queue) == queued_before

    async def test_running_job_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1})
        await _set_running(db_session, document_id, job_id)

        queued_before = len(fake_queue)
        response = await _rotate(client, auth_headers, document_id, 1, 90)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ALREADY_PROCESSING"
        assert len(fake_queue) == queued_before

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
        await _finish(db_session, document_id, job_id, {1})
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await _rotate(client, other, document_id, 1, 90)

        assert response.status_code == 404

    async def test_requires_sign_in(self, client):
        response = await _rotate(client, {}, uuid.uuid4(), 1, 90)
        assert response.status_code == 401
