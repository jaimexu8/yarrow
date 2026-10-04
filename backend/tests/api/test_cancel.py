"""US-42: cancel a document's processing job while it is still queued."""

import uuid

import pytest
from yarrow_db.models import Document, Job

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records revoked task ids instead of talking to Celery."""
    from app.api.v1.endpoints import documents

    calls: list[str] = []
    monkeypatch.setattr(documents, "revoke_document_processing", calls.append)
    return calls


async def _upload(client, headers) -> tuple[str, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return accepted["document_id"], uuid.UUID(accepted["job_id"])


def _cancel_url(document_id: str) -> str:
    return f"{DOCUMENTS}{document_id}/cancel"


async def _set_status(db_session, job_id, status: str) -> None:
    """What the worker writes as it picks up or finishes a job."""
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = status
    await db_session.flush()


class TestCancelQueued:
    async def test_queued_job_is_canceled_and_revoked(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)

        response = await client.post(_cancel_url(document_id), headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "canceled"
        job = await db_session.get(Job, job_id)
        await db_session.refresh(job)
        assert job.status == "canceled"
        # Dropped from the queue by the task id saved at upload.
        assert revoked == [job.celery_task_id] == ["task-1"]

    async def test_canceled_state_is_shown(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        document_id, _ = await _upload(client, auth_headers)
        await client.post(_cancel_url(document_id), headers=auth_headers)

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()

        assert library[0]["status"] == "canceled"
        assert library[0]["error_message"] is None
        assert detail["status"] == "canceled"
        assert detail["jobs"][0]["status"] == "canceled"

    async def test_cancel_stands_even_if_revoke_fails(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        """The worker checks the row before starting, so a broker hiccup
        while revoking must not undo or fail the cancel."""
        from app.api.v1.endpoints import documents

        def _broker_down(task_id):
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(documents, "revoke_document_processing", _broker_down)
        document_id, _ = await _upload(client, auth_headers)

        response = await client.post(_cancel_url(document_id), headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["status"] == "canceled"


class TestOnlyQueuedCanBeCanceled:
    @pytest.mark.parametrize("status", ["processing", "completed", "failed"])
    async def test_started_or_finished_job_cannot_be_canceled(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        db_session,
        status,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, status)

        response = await client.post(_cancel_url(document_id), headers=auth_headers)

        assert response.status_code == 409
        assert revoked == []
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library[0]["status"] == status

    async def test_canceling_twice_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        document_id, _ = await _upload(client, auth_headers)
        await client.post(_cancel_url(document_id), headers=auth_headers)

        again = await client.post(_cancel_url(document_id), headers=auth_headers)

        assert again.status_code == 409
        assert len(revoked) == 1


class TestOwnership:
    async def test_requires_auth(self, client):
        response = await client.post(_cancel_url(str(uuid.uuid4())))
        assert response.status_code == 401

    async def test_someone_elses_document_is_404_and_untouched(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
        revoked,
    ):
        document_id, _ = await _upload(client, auth_headers)
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.post(_cancel_url(document_id), headers=other)

        assert response.status_code == 404
        assert revoked == []
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library[0]["status"] == "queued"
