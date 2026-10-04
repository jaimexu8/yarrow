"""US-43: reprocess a document whose processing finished or was canceled."""

import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Job

from app.services.failure_messages import QUEUE_FAILED_MESSAGE
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers) -> tuple[str, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return accepted["document_id"], uuid.UUID(accepted["job_id"])


def _reprocess_url(document_id: str) -> str:
    return f"{DOCUMENTS}{document_id}/reprocess"


async def _set_status(db_session, job_id, status: str) -> None:
    """What the worker writes as it picks up or finishes a job."""
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = status
    await db_session.flush()


async def _jobs_for(db_session, document_id: str) -> list[Job]:
    result = await db_session.execute(
        select(Job).where(Job.document_id == document_id).order_by(Job.created_at)
    )
    return list(result.scalars().all())


class TestReprocessFinishedOrCanceled:
    @pytest.mark.parametrize("status", ["failed", "completed", "canceled"])
    async def test_finished_or_canceled_document_is_reprocessed(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
        status,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, status)

        response = await client.post(_reprocess_url(document_id), headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        assert response.json()["error_message"] is None

        # The finished job stays as history; only a new row is added.
        original = await db_session.get(Job, job_id)
        await db_session.refresh(original)
        assert original.status == status
        jobs = await _jobs_for(db_session, document_id)
        assert len(jobs) == 2
        new_job = max(jobs, key=lambda job: job.created_at)
        assert new_job.id != original.id
        assert new_job.status == "queued"
        assert new_job.celery_task_id == "task-2"
        # The upload enqueued the original first.
        assert fake_queue == [job_id, new_job.id]

        detail = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()
        assert detail["status"] == "queued"
        assert len(detail["jobs"]) == 2
        assert detail["jobs"][1]["status"] == "queued"


class TestOnlyFinishedOrCanceledCanBeReprocessed:
    @pytest.mark.parametrize("status", ["queued", "processing"])
    async def test_active_job_cannot_be_reprocessed(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
        status,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, status)

        response = await client.post(_reprocess_url(document_id), headers=auth_headers)

        assert response.status_code == 409
        assert len(await _jobs_for(db_session, document_id)) == 1
        assert len(fake_queue) == 1

    async def test_reprocessing_twice_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "failed")

        first = await client.post(_reprocess_url(document_id), headers=auth_headers)
        again = await client.post(_reprocess_url(document_id), headers=auth_headers)

        assert first.status_code == 200
        assert again.status_code == 409
        assert len(await _jobs_for(db_session, document_id)) == 2
        assert len(fake_queue) == 2


class TestQueueOutage:
    async def test_reprocess_fails_when_the_broker_is_down(
        self, client, auth_headers, fake_storage, monkeypatch, db_session
    ):
        """The new job is already committed when the broker call fails, so a
        dead broker must not lose it: it is marked failed, like an upload
        whose enqueue fails."""
        from app.api.v1.endpoints import documents

        def _broker_down(job_id):
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(documents, "enqueue_document_processing", _broker_down)
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "failed")

        response = await client.post(_reprocess_url(document_id), headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["error_message"] == QUEUE_FAILED_MESSAGE
        jobs = await _jobs_for(db_session, document_id)
        assert len(jobs) == 2
        new_job = next(job for job in jobs if job.id != job_id)
        assert new_job.status == "failed"
        assert new_job.error_message == QUEUE_FAILED_MESSAGE


class TestOwnership:
    async def test_requires_auth(self, client):
        response = await client.post(_reprocess_url(str(uuid.uuid4())))
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
        db_session,
    ):
        document_id, _ = await _upload(client, auth_headers)
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.post(_reprocess_url(document_id), headers=other)

        assert response.status_code == 404
        assert len(await _jobs_for(db_session, document_id)) == 1
        assert len(fake_queue) == 1
