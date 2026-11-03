"""us-74: admin list of processing jobs across users."""

import uuid

from yarrow_db.models import Job, User

from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    QUEUE_FAILED_MESSAGE,
)
from tests.conftest import FIXTURES_DIR

JOBS = "/api/v1/admin/jobs"
UPLOAD = "/api/v1/documents/upload"


async def _promote(client, auth_headers, db_session):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return me


async def _other_headers(register, verify, login, name="Pat Lee"):
    payload, _ = await register(name=name)
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return payload, {"Authorization": f"Bearer {token['access_token']}"}


async def _upload(client, headers, filename="notes.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    uploaded = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (filename, pdf, "application/pdf"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()["accepted"][0]


class TestAdminJobs:
    async def test_requires_auth(self, client):
        assert (await client.get(JOBS)).status_code == 401

    async def test_non_admin_is_forbidden(self, client, auth_headers):
        response = await client.get(JOBS, headers=auth_headers)
        assert response.status_code == 403

    async def test_admin_sees_jobs_from_other_users(
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
        await _promote(client, auth_headers, db_session)
        other, other_headers = await _other_headers(register, verify, login)
        item = await _upload(client, other_headers, "notes.pdf")

        response = await client.get(JOBS, headers=auth_headers)
        assert response.status_code == 200
        row = next(job for job in response.json() if job["id"] == item["job_id"])
        assert row["document_id"] == item["document_id"]
        assert row["filename"] == "notes.pdf"
        assert row["owner_email"] == other["email"]
        assert row["owner_name"] == "Pat Lee"
        assert row["status"] == "queued"
        assert row["current_stage"] == "queued"

    async def test_queued_jobs_show_queue_place(
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
        await _promote(client, auth_headers, db_session)
        _, other_headers = await _other_headers(register, verify, login)
        first = await _upload(client, other_headers, "alpha.pdf")
        second = await _upload(client, other_headers, "zeta.pdf")

        rows = {
            job["id"]: job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
        }
        assert rows[first["job_id"]]["queue_position"] == 1
        assert rows[second["job_id"]]["queue_position"] == 2

    async def test_running_job_shows_queue_place_and_stage(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
    ):
        await _promote(client, auth_headers, db_session)
        item = await _upload(client, auth_headers)
        job = await db_session.get(Job, uuid.UUID(item["job_id"]))
        job.status = "processing"
        job.current_stage = "parsing"
        await db_session.flush()

        row = next(
            job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
            if job["id"] == item["job_id"]
        )
        assert row["status"] == "processing"
        assert row["current_stage"] == "parsing"
        assert row["queue_position"] is None

    async def test_failed_job_shows_why_it_failed(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
    ):
        await _promote(client, auth_headers, db_session)
        item = await _upload(client, auth_headers)
        job = await db_session.get(Job, uuid.UUID(item["job_id"]))
        job.status = "failed"
        job.error_message = QUEUE_FAILED_MESSAGE
        await db_session.flush()

        row = next(
            job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
            if job["id"] == item["job_id"]
        )
        assert row["status"] == "failed"
        assert row["error_message"] == QUEUE_FAILED_MESSAGE

    async def test_internal_failure_is_replaced(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
    ):
        await _promote(client, auth_headers, db_session)
        item = await _upload(client, auth_headers)
        job = await db_session.get(Job, uuid.UUID(item["job_id"]))
        job.status = "failed"
        job.error_message = "KeyError: 'missing'"
        await db_session.flush()

        row = next(
            job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
            if job["id"] == item["job_id"]
        )
        assert row["error_message"] == DOCUMENT_FAILED_MESSAGE
