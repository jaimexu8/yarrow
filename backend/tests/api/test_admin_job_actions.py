"""US-75: admin force-cancel and retry of jobs across every user's library."""

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Job, JobPage, Page, User

from app.api.v1.endpoints.uploads import MIN_CHUNK_BYTES
from tests.conftest import FIXTURES_DIR

JOBS = "/api/v1/admin/jobs"
UPLOAD = "/api/v1/documents/upload"
UPLOADS = "/api/v1/uploads"


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    from app.api.v1.endpoints import admin, documents

    calls: list[tuple[str, bool]] = []

    def _revoke(task_id, terminate=False):
        calls.append((task_id, terminate))

    monkeypatch.setattr(admin, "revoke_document_processing", _revoke)
    monkeypatch.setattr(documents, "revoke_document_processing", _revoke)
    return calls


async def _promote(client, headers, db_session) -> uuid.UUID:
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return user.id


async def _other_headers(register, verify, login):
    payload, _ = await register(name="Pat Lee")
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        data={"allow_duplicate": "true"},
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _upload_in_batch(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        data={"allow_duplicate": "true", "batch_id": str(uuid.uuid4())},
        files=[
            ("files", ("first.pdf", pdf, "application/pdf")),
            ("files", ("second.pdf", pdf, "application/pdf")),
        ],
    )
    assert response.status_code == 200, response.text
    accepted = response.json()["accepted"][-1]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _upload_resumable(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    head = (FIXTURES_DIR / "sample.pdf").read_bytes()
    content = head + os.urandom(MIN_CHUNK_BYTES * 2 + 1234 - len(head))
    created = await client.post(
        UPLOADS,
        headers=headers,
        json={
            "filename": "scan.pdf",
            "total_size_bytes": len(content),
            "chunk_size_bytes": MIN_CHUNK_BYTES,
            "batch_id": str(uuid.uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    for index, start in enumerate(range(0, len(content), MIN_CHUNK_BYTES)):
        data = content[start : start + MIN_CHUNK_BYTES]
        sent = await client.put(
            f"{UPLOADS}/{session_id}/chunks/{index}",
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
            },
            content=data,
        )
        assert sent.status_code == 200, sent.text
    done = await client.post(f"{UPLOADS}/{session_id}/finalize", headers=headers)
    assert done.status_code == 200, done.text
    return uuid.UUID(done.json()["document_id"]), uuid.UUID(done.json()["job_id"])


UPLOAD_PATHS = {
    "single": _upload,
    "batch": _upload_in_batch,
    "resumable": _upload_resumable,
}


async def _listed(client, headers, job_id) -> dict:
    jobs = (await client.get(JOBS, headers=headers)).json()
    return next(job for job in jobs if job["id"] == str(job_id))


async def _set_status(db_session, job_id, status, updated_at=None):
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = status
    if updated_at is not None:
        job.updated_at = updated_at
    await db_session.flush()


async def _fail_with_pages(db_session, document_id, job_id, states: dict[int, str]):
    document = await db_session.get(Document, document_id)
    document.page_count = len(states)
    for number, state in states.items():
        db_session.add(Page(document_id=document_id, page_number=number, status=state))
    await _set_status(db_session, job_id, "failed")


@pytest.fixture
async def setup(
    client,
    auth_headers,
    register,
    verify,
    login,
    db_session,
    fake_storage,
    fake_queue,
    revoked,
):
    admin_id = await _promote(client, auth_headers, db_session)
    other = await _other_headers(register, verify, login)
    return admin_id, other


class TestForceCancel:
    @pytest.mark.parametrize("status", ["queued", "processing"])
    async def test_admin_cancels_another_users_job(
        self, client, auth_headers, setup, db_session, revoked, status
    ):
        admin_id, other = setup
        document_id, job_id = await _upload(client, other)
        await _set_status(db_session, job_id, status)
        db_session.add_all(
            [
                JobPage(job_id=job_id, page_number=1, status="completed"),
                JobPage(job_id=job_id, page_number=2, status="processing"),
                JobPage(job_id=job_id, page_number=3, status="queued"),
            ]
        )
        await db_session.flush()

        response = await client.post(
            f"{JOBS}/{job_id}/cancel",
            headers=auth_headers,
            json={"reason": "Stuck in parsing for 2 hours"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "job_id": str(job_id),
            "document_id": str(document_id),
            "status": "canceled",
            "new_job_id": None,
            "new_job_status": None,
            "scope": None,
            "pages": None,
        }
        job = await db_session.get(Job, job_id, populate_existing=True)
        document = await db_session.get(Document, document_id, populate_existing=True)
        assert job.status == job.current_stage == "canceled"
        assert job.canceled_by_id == admin_id
        assert job.cancel_reason == "Stuck in parsing for 2 hours"
        assert document.status == "canceled"
        assert document.error_message is None
        pages = (
            await db_session.execute(
                select(JobPage.page_number, JobPage.status)
                .where(JobPage.job_id == job_id)
                .order_by(JobPage.page_number)
                .execution_options(populate_existing=True)
            )
        ).all()
        assert [tuple(row) for row in pages] == [
            (1, "completed"),
            (2, "canceled"),
            (3, "canceled"),
        ]
        assert revoked == [(job.celery_task_id, True)]

    async def test_stuck_job_is_flagged_and_cancelable(
        self, client, auth_headers, setup, db_session, revoked
    ):
        _, other = setup
        _, job_id = await _upload(client, other)
        await _set_status(
            db_session,
            job_id,
            "processing",
            datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        )

        row = next(
            job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
            if job["id"] == str(job_id)
        )
        assert row["is_stuck"] is True
        assert row["can_cancel"] is True
        assert row["can_retry"] is False

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "canceled"

    async def test_body_is_optional(self, client, auth_headers, setup, db_session):
        _, other = setup
        _, job_id = await _upload(client, other)

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)

        assert response.status_code == 200, response.text
        job = await db_session.get(Job, job_id, populate_existing=True)
        assert job.cancel_reason is None

    async def test_cancel_stands_if_revoke_fails(
        self, client, auth_headers, setup, db_session, monkeypatch
    ):
        from app.api.v1.endpoints import admin

        def _broken(task_id, terminate=False):
            raise ConnectionError("broker down")

        monkeypatch.setattr(admin, "revoke_document_processing", _broken)
        _, other = setup
        _, job_id = await _upload(client, other)

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)

        assert response.status_code == 200, response.text
        job = await db_session.get(Job, job_id, populate_existing=True)
        assert job.status == "canceled"

    @pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
    async def test_finished_job_is_not_cancelable(
        self, client, auth_headers, setup, db_session, revoked, status
    ):
        _, other = setup
        _, job_id = await _upload(client, other)
        await _set_status(db_session, job_id, status)

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOB_NOT_CANCELABLE"
        job = await db_session.get(Job, job_id, populate_existing=True)
        assert job.status == status
        assert revoked == []

    async def test_reason_is_limited_to_500_characters(
        self, client, auth_headers, setup
    ):
        _, other = setup
        _, job_id = await _upload(client, other)

        response = await client.post(
            f"{JOBS}/{job_id}/cancel", headers=auth_headers, json={"reason": "x" * 501}
        )

        assert response.status_code == 422

    async def test_unknown_job_is_404(self, client, auth_headers, setup):
        response = await client.post(
            f"{JOBS}/{uuid.uuid4()}/cancel", headers=auth_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"] == {
            "detail": "Job not found",
            "code": "JOB_NOT_FOUND",
        }

    async def test_trashed_document_job_is_404(
        self, client, auth_headers, setup, db_session
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        document = await db_session.get(Document, document_id)
        document.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await db_session.flush()

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "JOB_NOT_FOUND"


class TestRetry:
    async def test_retry_queues_one_job_and_keeps_completed_pages(
        self, client, auth_headers, setup, db_session, fake_queue
    ):
        admin_id, other = setup
        document_id, job_id = await _upload(client, other)
        await _fail_with_pages(
            db_session,
            document_id,
            job_id,
            {1: "completed", 2: "failed", 3: "completed", 4: "failed"},
        )
        queued_before = len(fake_queue)

        response = await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)

        assert response.status_code == 201, response.text
        body = response.json()
        assert len(fake_queue) == queued_before + 1
        new_job_id = fake_queue[-1]
        assert body == {
            "job_id": str(job_id),
            "document_id": str(document_id),
            "status": "failed",
            "new_job_id": str(new_job_id),
            "new_job_status": "queued",
            "scope": "incomplete",
            "pages": [2, 4],
        }
        assert fake_queue.pages[new_job_id] == [2, 4]
        new_job = await db_session.get(Job, new_job_id, populate_existing=True)
        assert new_job.status == "queued"
        assert new_job.retry_of_job_id == job_id
        assert new_job.requested_by_id == admin_id
        completed = list(
            await db_session.scalars(
                select(Page.page_number)
                .where(Page.document_id == document_id, Page.status == "completed")
                .order_by(Page.page_number)
            )
        )
        assert completed == [1, 3]
        document = await db_session.get(Document, document_id, populate_existing=True)
        assert document.status == "queued"

    async def test_retry_flags_in_job_list(
        self, client, auth_headers, setup, db_session
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        await _fail_with_pages(db_session, document_id, job_id, {1: "failed"})

        rows = {
            job["id"]: job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
        }
        assert rows[str(job_id)]["can_retry"] is True
        assert rows[str(job_id)]["can_cancel"] is False

        new_job_id = (
            await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)
        ).json()["new_job_id"]

        rows = {
            job["id"]: job
            for job in (await client.get(JOBS, headers=auth_headers)).json()
        }
        assert rows[str(job_id)]["can_retry"] is False
        assert rows[new_job_id]["retry_of_job_id"] == str(job_id)
        assert rows[new_job_id]["can_cancel"] is True

    async def test_retry_of_non_latest_job_is_409(
        self, client, auth_headers, setup, db_session, fake_queue
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        await _fail_with_pages(db_session, document_id, job_id, {1: "failed"})
        assert (
            await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)
        ).status_code == 201
        await _set_status(db_session, fake_queue[-1], "failed")
        queued_before = len(fake_queue)

        response = await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOB_NOT_RETRYABLE"
        assert len(fake_queue) == queued_before

    @pytest.mark.parametrize(
        "status", ["queued", "processing", "completed", "canceled"]
    )
    async def test_retry_of_non_failed_job_is_409(
        self, client, auth_headers, setup, db_session, fake_queue, status
    ):
        _, other = setup
        _, job_id = await _upload(client, other)
        await _set_status(db_session, job_id, status)
        queued_before = len(fake_queue)

        response = await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOB_NOT_RETRYABLE"
        assert len(fake_queue) == queued_before

    async def test_queue_down_is_503(
        self, client, auth_headers, setup, db_session, monkeypatch
    ):
        from app.api.v1.endpoints import admin

        def _broker_down(job_id, pages=None, **_kwargs):
            raise ConnectionError("broker down")

        monkeypatch.setattr(admin, "enqueue_document_processing", _broker_down)
        _, other = setup
        document_id, job_id = await _upload(client, other)
        await _fail_with_pages(db_session, document_id, job_id, {1: "failed"})

        response = await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"
        document = await db_session.get(Document, document_id, populate_existing=True)
        assert document.status == "failed"

    async def test_unknown_job_is_404(self, client, auth_headers, setup):
        response = await client.post(
            f"{JOBS}/{uuid.uuid4()}/retry", headers=auth_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "JOB_NOT_FOUND"


class TestPermissions:
    @pytest.mark.parametrize("action", ["cancel", "retry"])
    async def test_unauthenticated_is_401(self, client, action):
        response = await client.post(f"{JOBS}/{uuid.uuid4()}/{action}")
        assert response.status_code == 401

    @pytest.mark.parametrize("action", ["cancel", "retry"])
    async def test_non_admin_is_forbidden_for_another_users_job(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        db_session,
        fake_storage,
        fake_queue,
        revoked,
        action,
    ):
        other = await _other_headers(register, verify, login)
        document_id, job_id = await _upload(client, other)
        if action == "retry":
            await _fail_with_pages(db_session, document_id, job_id, {1: "failed"})

        response = await client.post(f"{JOBS}/{job_id}/{action}", headers=auth_headers)

        assert response.status_code == 403
        assert response.json() == {"detail": "The user doesn't have enough privileges"}
        job = await db_session.get(Job, job_id, populate_existing=True)
        assert job.status == ("failed" if action == "retry" else "queued")
        assert revoked == []

    @pytest.mark.parametrize("action", ["cancel", "retry"])
    async def test_non_admin_is_forbidden_for_own_job(
        self,
        client,
        auth_headers,
        db_session,
        fake_storage,
        fake_queue,
        revoked,
        action,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        if action == "retry":
            await _fail_with_pages(db_session, document_id, job_id, {1: "failed"})
        queued_before = len(fake_queue)

        response = await client.post(f"{JOBS}/{job_id}/{action}", headers=auth_headers)

        assert response.status_code == 403
        assert len(fake_queue) == queued_before
        assert revoked == []


class TestRequestedBy:
    async def test_upload_reprocess_and_user_cancel_record_the_user(
        self, client, auth_headers, db_session, fake_storage, fake_queue, revoked
    ):
        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
        user_id = uuid.UUID(me["id"])
        document_id, job_id = await _upload(client, auth_headers)
        job = await db_session.get(Job, job_id)
        assert job.requested_by_id == user_id

        await client.post(
            f"/api/v1/documents/{document_id}/cancel", headers=auth_headers
        )
        job = await db_session.get(Job, job_id, populate_existing=True)
        assert job.canceled_by_id == user_id
        assert revoked == [(job.celery_task_id, False)]

        await client.post(
            f"/api/v1/documents/{document_id}/reprocess", headers=auth_headers
        )
        new_job = await db_session.get(Job, fake_queue[-1], populate_existing=True)
        assert new_job.requested_by_id == user_id
        assert new_job.retry_of_job_id is None


class TestFreshUpload:
    @pytest.mark.parametrize("path", sorted(UPLOAD_PATHS))
    @pytest.mark.parametrize(
        ("status", "stage"),
        [
            ("queued", "queued"),
            ("processing", "downloading"),
            ("processing", "preparing"),
            ("processing", "parsing"),
        ],
    )
    async def test_active_job_of_a_fresh_upload_can_be_force_canceled(
        self, client, auth_headers, setup, db_session, revoked, path, status, stage
    ):
        _, other = setup
        document_id, job_id = await UPLOAD_PATHS[path](client, other)
        job = await db_session.get(Job, job_id)
        job.status = status
        job.current_stage = stage
        await db_session.flush()

        jobs = (await client.get(JOBS, headers=auth_headers)).json()
        assert [
            row["id"] for row in jobs if row["document_id"] == str(document_id)
        ] == [str(job_id)]
        assert (await _listed(client, auth_headers, job_id))["can_cancel"] is True

        response = await client.post(
            f"{JOBS}/{job_id}/cancel", headers=auth_headers, json={}
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "canceled"
        assert (await _listed(client, auth_headers, job_id))["can_cancel"] is False

    @pytest.mark.parametrize(
        ("status", "detail"),
        [
            (
                "failed",
                "This job has already failed, so there is nothing left to cancel.",
            ),
            (
                "completed",
                "This job has already finished, so there is nothing left to cancel.",
            ),
            ("canceled", "This job has already been canceled."),
        ],
    )
    async def test_job_that_ends_after_the_list_loaded_explains_why(
        self, client, auth_headers, setup, db_session, revoked, status, detail
    ):
        _, other = setup
        _, job_id = await _upload(client, other)
        assert (await _listed(client, auth_headers, job_id))["can_cancel"] is True
        await _set_status(db_session, job_id, status)

        response = await client.post(
            f"{JOBS}/{job_id}/cancel", headers=auth_headers, json={}
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "detail": detail,
            "code": "JOB_NOT_CANCELABLE",
        }
        assert (await _listed(client, auth_headers, job_id))["can_cancel"] is False
        assert revoked == []

    async def test_superseded_job_explains_why(
        self, client, auth_headers, setup, db_session, revoked
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        older = await db_session.get(Job, job_id)
        newer = Job(
            document_id=document_id,
            status="queued",
            current_stage="queued",
            created_at=older.created_at + timedelta(seconds=1),
        )
        db_session.add(newer)
        await db_session.flush()

        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "detail": "A newer job has replaced this one, so it can no longer be canceled.",
            "code": "JOB_NOT_CANCELABLE",
        }

    async def test_list_and_cancel_pick_the_same_latest_job_on_a_created_at_tie(
        self, client, auth_headers, setup, db_session, revoked
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        first = await db_session.get(Job, job_id)
        twin = Job(
            document_id=document_id,
            status="queued",
            current_stage="queued",
            created_at=first.created_at,
        )
        db_session.add(twin)
        await db_session.flush()

        jobs = (await client.get(JOBS, headers=auth_headers)).json()
        flags = {
            row["id"]: row["can_cancel"]
            for row in jobs
            if row["document_id"] == str(document_id)
        }
        assert sorted(flags.values()) == [False, True]

        for listed_id, can_cancel in flags.items():
            response = await client.post(
                f"{JOBS}/{listed_id}/cancel", headers=auth_headers
            )
            assert response.status_code == (200 if can_cancel else 409), response.text

    async def test_job_without_created_at_does_not_hide_the_latest_one(
        self, client, auth_headers, setup, db_session, revoked
    ):
        _, other = setup
        document_id, job_id = await _upload(client, other)
        legacy = Job(document_id=document_id, status="failed", current_stage="failed")
        db_session.add(legacy)
        await db_session.flush()
        legacy.created_at = None
        await db_session.flush()

        assert (await _listed(client, auth_headers, job_id))["can_cancel"] is True
        response = await client.post(f"{JOBS}/{job_id}/cancel", headers=auth_headers)
        assert response.status_code == 200, response.text


class TestRetryRefusals:
    async def test_job_that_is_no_longer_failed_explains_why(
        self, client, auth_headers, setup, db_session
    ):
        _, other = setup
        _, job_id = await _upload(client, other)
        await _set_status(db_session, job_id, "completed")

        response = await client.post(f"{JOBS}/{job_id}/retry", headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "detail": "Only a failed job can be retried, and this one is completed.",
            "code": "JOB_NOT_RETRYABLE",
        }
