"""us-73: admin dashboard totals."""

import uuid

from yarrow_db.models import Job, User

from tests.conftest import FIXTURES_DIR

STATS = "/api/v1/admin/stats"
UPLOAD = "/api/v1/documents/upload"

EMPTY_JOBS = {
    "queued": 0,
    "processing": 0,
    "completed": 0,
    "failed": 0,
    "canceled": 0,
}


async def _promote(client, auth_headers, db_session):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return me


async def _other_headers(register, verify, login):
    payload, _ = await register(name="Pat Lee")
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return payload, {"Authorization": f"Bearer {token['access_token']}"}


class TestAdminStats:
    async def test_requires_auth(self, client):
        assert (await client.get(STATS)).status_code == 401

    async def test_non_admin_is_forbidden(self, client, auth_headers):
        response = await client.get(STATS, headers=auth_headers)
        assert response.status_code == 403

    async def test_empty_system_totals(
        self, client, auth_headers, db_session
    ):
        await _promote(client, auth_headers, db_session)
        response = await client.get(STATS, headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["user_count"] == 1
        assert body["document_count"] == 0
        assert body["storage_used_bytes"] == 0
        assert body["jobs_by_status"] == EMPTY_JOBS

    async def test_counts_users_documents_storage_and_jobs(
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
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        uploaded = await client.post(
            UPLOAD,
            headers=other_headers,
            files=[("files", ("notes.pdf", pdf, "application/pdf"))],
        )
        assert uploaded.status_code == 200, uploaded.text

        response = await client.get(STATS, headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["user_count"] == 2
        assert body["document_count"] == 1
        assert body["storage_used_bytes"] == len(pdf)
        assert body["jobs_by_status"]["queued"] == 1
        assert body["jobs_by_status"]["failed"] == 0

    async def test_jobs_by_status_after_failure(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
    ):
        await _promote(client, auth_headers, db_session)
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        uploaded = await client.post(
            UPLOAD,
            headers=auth_headers,
            files=[("files", ("notes.pdf", pdf, "application/pdf"))],
        )
        assert uploaded.status_code == 200, uploaded.text
        job_id = uploaded.json()["accepted"][0]["job_id"]
        job = await db_session.get(Job, uuid.UUID(job_id))
        job.status = "failed"
        await db_session.flush()

        body = (await client.get(STATS, headers=auth_headers)).json()
        assert body["jobs_by_status"]["queued"] == 0
        assert body["jobs_by_status"]["failed"] == 1
        assert body["jobs_by_status"]["processing"] == 0
        assert body["jobs_by_status"]["completed"] == 0
        assert body["jobs_by_status"]["canceled"] == 0
