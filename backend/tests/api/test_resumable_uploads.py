"""US-80: resumable uploads of large files.

A session is created with the file's size and chunk size, chunks are PUT by
index, and finalize assembles them into one document with one queued job.
Chunks are kept as temporary objects (FakeStorage here) under uploads/.
"""

import hashlib
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from yarrow_db.models import Batch, Document, Job, UploadChunk, UploadSession, User

from app.api.v1.endpoints.uploads import MIN_CHUNK_BYTES
from app.core.config import settings
from app.services.upload_sessions import purge_expired_upload_sessions, utcnow
from tests.conftest import DEFAULT_PASSWORD, FIXTURES_DIR, FakeStorage

UPLOADS = "/api/v1/uploads"
CHUNK = MIN_CHUNK_BYTES


def _pdf_bytes(size: int = CHUNK * 3 + 1234) -> bytes:
    """A real PDF header followed by padding, so the type sniffs as PDF and
    the file spans several chunks with a short last one."""
    head = (FIXTURES_DIR / "sample.pdf").read_bytes()
    return head + os.urandom(size - len(head))


def _chunks(content: bytes, chunk: int = CHUNK) -> list[bytes]:
    return [content[i : i + chunk] for i in range(0, len(content), chunk)]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _create(client, headers, content: bytes, **extra):
    body = {
        "filename": extra.pop("filename", "scan.pdf"),
        "total_size_bytes": extra.pop("total_size_bytes", len(content)),
        "chunk_size_bytes": extra.pop("chunk_size_bytes", CHUNK),
        **extra,
    }
    return await client.post(UPLOADS, headers=headers, json=body)


async def _put(client, headers, session_id, index, data: bytes, checksum=True):
    chunk_headers = dict(headers)
    chunk_headers["Content-Type"] = "application/octet-stream"
    if checksum is True:
        chunk_headers["X-Chunk-SHA256"] = _sha(data)
    elif checksum:
        chunk_headers["X-Chunk-SHA256"] = checksum
    return await client.put(
        f"{UPLOADS}/{session_id}/chunks/{index}", headers=chunk_headers, content=data
    )


async def _started(client, headers, content: bytes, **extra) -> str:
    response = await _create(client, headers, content, **extra)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _send_all(client, headers, session_id, content: bytes) -> None:
    for index, data in enumerate(_chunks(content)):
        response = await _put(client, headers, session_id, index, data)
        assert response.status_code == 200, response.text


def _chunk_objects(storage: FakeStorage) -> list[str]:
    return [key for key in storage.objects if key.startswith("uploads/")]


async def _user(db_session, headers, client) -> User:
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return await db_session.get(User, uuid.UUID(me["id"]))


async def _other_user(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class CountingStorage(FakeStorage):
    """Counts writes per key, to prove a retried chunk is not written twice."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: dict[str, int] = {}
        self.fail_next_document_write = False

    def upload_file(self, file, key: str) -> None:
        if self.fail_next_document_write and key.startswith("documents/"):
            self.fail_next_document_write = False
            raise RuntimeError("s3://secret-bucket unreachable")
        self.writes[key] = self.writes.get(key, 0) + 1
        super().upload_file(file, key)


@pytest.fixture
def storage(fake_storage, monkeypatch) -> CountingStorage:
    from app.api.v1.endpoints import documents, uploads
    from app.services import account

    counting = CountingStorage()
    for module in (documents, uploads, account):
        monkeypatch.setattr(module, "get_storage", lambda: counting)
    return counting


# ---------------------------------------------------------------------------
# Criterion 1: an interrupted upload resumes from the last confirmed chunk.
# ---------------------------------------------------------------------------
class TestResume:
    async def test_interrupted_upload_resumes_from_last_confirmed_chunk(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        parts = _chunks(content)
        assert len(parts) == 4
        session_id = await _started(client, auth_headers, content)

        # Two chunks arrive, then the connection drops.
        for index in (0, 1):
            assert (
                await _put(client, auth_headers, session_id, index, parts[index])
            ).status_code == 200

        # On reconnecting, the browser asks what the server already has.
        state = await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert state.status_code == 200, state.text
        body = state.json()
        assert body["received_chunks"] == [0, 1]
        assert body["received_bytes"] == 2 * CHUNK
        assert body["chunk_count"] == 4
        assert body["status"] == "processing"

        # ...and sends only the rest.
        for index in (2, 3):
            assert (
                await _put(client, auth_headers, session_id, index, parts[index])
            ).status_code == 200
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text

        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert storage.objects[document.storage_key] == content
        # Every chunk was written exactly once: nothing was re-sent.
        chunk_writes = [n for k, n in storage.writes.items() if "/chunks/" in k]
        assert chunk_writes == [1, 1, 1, 1]

    async def test_chunk_cut_off_mid_transfer_is_not_confirmed(
        self, client, auth_headers, storage, fake_queue
    ):
        """A connection that drops inside a chunk leaves that chunk to resend."""
        content = _pdf_bytes()
        parts = _chunks(content)
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, parts[0])

        cut = await _put(
            client, auth_headers, session_id, 1, parts[1][:1000], checksum=False
        )
        assert cut.status_code == 400
        assert "incomplete" in cut.json()["detail"]

        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["received_chunks"] == [0]
        assert len(_chunk_objects(storage)) == 1

        assert (
            await _put(client, auth_headers, session_id, 1, parts[1])
        ).status_code == 200

    async def test_resume_works_from_a_fresh_page_load(
        self, client, auth_headers, storage, fake_queue
    ):
        """Nothing is kept per connection: only the session id is needed."""
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])

        from httpx import ASGITransport, AsyncClient

        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as reloaded:
            state = await reloaded.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert state.status_code == 200
        assert state.json()["received_chunks"] == [0]

    async def test_each_chunk_extends_the_retention_window(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        upload = await db_session.get(UploadSession, uuid.UUID(session_id))
        upload.expires_at = utcnow() + timedelta(minutes=5)
        await db_session.commit()

        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        await db_session.refresh(upload)
        assert upload.expires_at > utcnow() + timedelta(
            hours=settings.UPLOAD_SESSION_TTL_HOURS - 1
        )

    async def test_upload_past_the_retention_window_cannot_be_resumed(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        upload = await db_session.get(UploadSession, uuid.UUID(session_id))
        upload.expires_at = utcnow() - timedelta(seconds=1)
        await db_session.commit()

        state = await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert state.status_code == 410
        assert "Start the upload again" in state.json()["detail"]
        late = await _put(client, auth_headers, session_id, 1, _chunks(content)[1])
        assert late.status_code == 410
        final = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert final.status_code == 410

    async def test_someone_elses_upload_is_404(
        self, client, auth_headers, storage, register, verify, login
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        other = await _other_user(register, verify, login)

        url = f"{UPLOADS}/{session_id}"
        assert (await client.get(url, headers=other)).status_code == 404
        put = await _put(client, other, session_id, 0, _chunks(content)[0])
        assert put.status_code == 404
        assert (await client.post(f"{url}/finalize", headers=other)).status_code == 404
        assert (await client.delete(url, headers=other)).status_code == 404
        assert _chunk_objects(storage) == []
        # Still the owner's, untouched.
        assert (await client.get(url, headers=auth_headers)).status_code == 200

    async def test_requires_auth(self, client):
        response = await client.post(
            UPLOADS,
            json={"filename": "a.pdf", "total_size_bytes": 1, "chunk_size_bytes": 1},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Criterion 2: a retried chunk does not duplicate content.
# ---------------------------------------------------------------------------
class TestRetriedChunks:
    async def test_retried_chunk_is_accepted_without_duplicating_content(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        parts = _chunks(content)
        session_id = await _started(client, auth_headers, content)

        first = await _put(client, auth_headers, session_id, 0, parts[0])
        # The reply was lost to a timeout, so the browser sends it again.
        retry = await _put(client, auth_headers, session_id, 0, parts[0])
        assert first.status_code == retry.status_code == 200
        assert first.json()["already_received"] is False
        assert retry.json()["already_received"] is True
        assert retry.json()["received_bytes"] == CHUNK
        assert retry.json()["received_count"] == 1

        rows = await db_session.scalar(
            select(func.count())
            .select_from(UploadChunk)
            .where(UploadChunk.session_id == uuid.UUID(session_id))
        )
        assert rows == 1
        assert len(_chunk_objects(storage)) == 1
        assert sum(storage.writes.values()) == 1

        for index in range(1, len(parts)):
            await _put(client, auth_headers, session_id, index, parts[index])
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text
        assert done.json()["file_size_bytes"] == len(content)
        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert storage.objects[document.storage_key] == content

    async def test_different_bytes_for_a_confirmed_chunk_are_refused(
        self, client, auth_headers, storage
    ):
        content = _pdf_bytes()
        parts = _chunks(content)
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 1, parts[1])

        other = os.urandom(len(parts[1]))
        response = await _put(client, auth_headers, session_id, 1, other)
        assert response.status_code == 409
        assert "different content" in response.json()["detail"]
        [key] = _chunk_objects(storage)
        assert storage.objects[key] == parts[1]

    async def test_chunk_damaged_in_transit_is_refused(
        self, client, auth_headers, storage
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        response = await _put(
            client, auth_headers, session_id, 0, _chunks(content)[0], checksum="0" * 64
        )
        assert response.status_code == 400
        assert "damaged" in response.json()["detail"]
        assert _chunk_objects(storage) == []

    async def test_chunk_larger_than_declared_is_refused(
        self, client, auth_headers, storage
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        response = await _put(
            client, auth_headers, session_id, 0, _chunks(content)[0] + b"extra"
        )
        assert response.status_code == 413
        assert _chunk_objects(storage) == []

    async def test_chunk_index_out_of_range_is_refused(
        self, client, auth_headers, storage
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        for index in (-1, 4):
            response = await _put(client, auth_headers, session_id, index, b"x")
            assert response.status_code == 400
        assert _chunk_objects(storage) == []

    async def test_storage_error_on_a_chunk_is_retryable_and_hides_details(
        self, client, auth_headers, storage
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)

        def broken(file, key):
            raise RuntimeError("s3://secret-bucket access key AKIA...")

        storage.upload_file = broken
        response = await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        assert response.status_code == 503
        assert "secret" not in response.text
        assert _chunk_objects(storage) == []

        del storage.upload_file  # storage is back
        retry = await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        assert retry.status_code == 200
        assert retry.json()["already_received"] is False


# ---------------------------------------------------------------------------
# Criterion 3: finalize stores one document and queues exactly one job.
# ---------------------------------------------------------------------------
class TestFinalize:
    async def test_finalize_stores_one_document_and_queues_one_job(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)

        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["filename"] == "scan.pdf"
        assert body["status"] == "queued"
        assert body["task_id"] == "task-1"

        documents = (
            (
                await db_session.execute(
                    select(Document).where(Document.filename == "scan.pdf")
                )
            )
            .scalars()
            .all()
        )
        assert len(documents) == 1
        document = documents[0]
        assert document.file_type == "application/pdf"
        assert document.file_size_bytes == len(content)
        jobs = (
            (
                await db_session.execute(
                    select(Job).where(Job.document_id == document.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].celery_task_id == "task-1"
        assert list(fake_queue) == [jobs[0].id]
        assert storage.objects[document.storage_key] == content

        # Temporary chunks are cleaned up; the quota counts the file once.
        assert _chunk_objects(storage) == []
        user = await _user(db_session, auth_headers, client)
        assert user.storage_used_bytes == len(content)
        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["status"] == "completed"
        assert state["document_id"] == str(document.id)

    async def test_finalizing_twice_returns_the_same_document_and_queues_once(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)

        url = f"{UPLOADS}/{session_id}/finalize"
        first = await client.post(url, headers=auth_headers)
        second = await client.post(url, headers=auth_headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["document_id"] == second.json()["document_id"]
        assert first.json()["job_id"] == second.json()["job_id"]
        assert len(fake_queue) == 1
        count = await db_session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.filename == "scan.pdf")
        )
        assert count == 1
        user = await _user(db_session, auth_headers, client)
        assert user.storage_used_bytes == len(content)

    async def test_chunks_after_finalize_are_refused(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        await client.post(f"{UPLOADS}/{session_id}/finalize", headers=auth_headers)

        late = await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        assert late.status_code == 409

    async def test_finalize_with_missing_chunks_is_refused(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])

        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "3 of 4 parts have not been uploaded yet."
        assert len(fake_queue) == 0
        assert (await db_session.scalar(select(func.count()).select_from(Job))) == 0

    async def test_storage_failure_at_finalize_can_be_retried(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """Recovery: the session survives a failed finalize, and the retry
        still makes only one document and one job."""
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)

        storage.fail_next_document_write = True
        url = f"{UPLOADS}/{session_id}/finalize"
        failed = await client.post(url, headers=auth_headers)
        assert failed.status_code == 503
        assert "secret" not in failed.text
        assert len(fake_queue) == 0

        retried = await client.post(url, headers=auth_headers)
        assert retried.status_code == 200, retried.text
        assert len(fake_queue) == 1
        assert (await db_session.scalar(select(func.count()).select_from(Job))) == 1

    async def test_queue_failure_keeps_the_document_and_reports_it(
        self, client, auth_headers, storage, fake_queue, monkeypatch, db_session
    ):
        from app.api.v1.endpoints import uploads

        def broken(job_id, pages=None, priority=None):
            raise RuntimeError("redis://valkey down")

        monkeypatch.setattr(uploads, "enqueue_document_processing", broken)
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)

        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200
        assert done.json()["status"] == "failed"
        assert "valkey" not in done.text
        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert document.status == "failed"

    async def test_finalized_upload_joins_its_batch(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """US-31: a large file counts in the run it was uploaded with."""
        batch_id = uuid.uuid4()
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        small = await client.post(
            "/api/v1/documents/upload",
            headers=auth_headers,
            data={"batch_id": str(batch_id)},
            files=[("files", ("small.pdf", pdf, "application/pdf"))],
        )
        assert small.status_code == 200, small.text

        content = _pdf_bytes()
        session_id = await _started(
            client, auth_headers, content, batch_id=str(batch_id)
        )
        await _send_all(client, auth_headers, session_id, content)
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text

        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert document.batch_id == batch_id
        progress = await client.get(
            f"/api/v1/documents/batches/{batch_id}", headers=auth_headers
        )
        assert progress.json()["total"] == 2
        assert (await db_session.scalar(select(func.count()).select_from(Batch))) == 1

    async def test_large_file_alone_creates_its_batch_at_finalize(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        batch_id = uuid.uuid4()
        content = _pdf_bytes()
        session_id = await _started(
            client, auth_headers, content, batch_id=str(batch_id)
        )
        # Nothing is stored yet, so no empty batch exists.
        assert await db_session.get(Batch, batch_id) is None
        await _send_all(client, auth_headers, session_id, content)
        await client.post(f"{UPLOADS}/{session_id}/finalize", headers=auth_headers)
        assert (await db_session.get(Batch, batch_id)) is not None

    async def test_cannot_upload_into_someone_elses_batch(
        self, client, auth_headers, storage, fake_queue, register, verify, login
    ):
        other = await _other_user(register, verify, login)
        batch_id = uuid.uuid4()
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        await client.post(
            "/api/v1/documents/upload",
            headers=other,
            data={"batch_id": str(batch_id)},
            files=[("files", ("theirs.pdf", pdf, "application/pdf"))],
        )
        response = await _create(
            client, auth_headers, _pdf_bytes(), batch_id=str(batch_id)
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Criterion 4: quota and validation failures are rejected with a clear error.
# ---------------------------------------------------------------------------
class TestQuotaAndValidation:
    async def test_upload_that_would_exceed_the_quota_is_rejected_up_front(
        self, client, auth_headers, storage, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", 10 * CHUNK)
        user = await _user(db_session, auth_headers, client)
        user.storage_used_bytes = 8 * CHUNK
        await db_session.commit()

        response = await _create(client, auth_headers, _pdf_bytes())
        assert response.status_code == 413
        assert response.json()["detail"] == (
            "Not enough storage left in your account for this file."
        )
        assert (
            await db_session.scalar(select(func.count()).select_from(UploadSession))
        ) == 0

    async def test_unfinished_uploads_count_toward_the_quota(
        self, client, auth_headers, storage, monkeypatch
    ):
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", 7 * CHUNK)
        content = _pdf_bytes()  # about 3 chunks
        await _started(client, auth_headers, content)
        await _started(client, auth_headers, content, filename="second.pdf")

        third = await _create(client, auth_headers, content, filename="third.pdf")
        assert third.status_code == 413
        assert "have not finished" in third.json()["detail"]

    async def test_aborting_an_upload_frees_its_reserved_space(
        self, client, auth_headers, storage, monkeypatch
    ):
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", 4 * CHUNK)
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        assert (await _create(client, auth_headers, content)).status_code == 413

        await client.delete(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert (await _create(client, auth_headers, content)).status_code == 201

    async def test_quota_is_checked_again_at_finalize(
        self, client, auth_headers, storage, fake_queue, db_session, monkeypatch
    ):
        """Space used by other uploads meanwhile is caught under the lock; the
        session stays open so the user can free space and finish it."""
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", 10 * CHUNK)
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)

        user = await _user(db_session, auth_headers, client)
        user.storage_used_bytes = 9 * CHUNK
        await db_session.commit()

        url = f"{UPLOADS}/{session_id}/finalize"
        response = await client.post(url, headers=auth_headers)
        assert response.status_code == 413
        assert "Not enough storage" in response.json()["detail"]
        assert len(fake_queue) == 0
        assert [k for k in storage.objects if k.startswith("documents/")] == []
        assert (await db_session.scalar(select(func.count()).select_from(Job))) == 0

        await db_session.refresh(user)
        user.storage_used_bytes = 0
        await db_session.commit()
        assert (await client.post(url, headers=auth_headers)).status_code == 200
        assert len(fake_queue) == 1

    async def test_chunk_is_refused_once_the_quota_is_used_up(
        self, client, auth_headers, storage, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", 10 * CHUNK)
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        user = await _user(db_session, auth_headers, client)
        user.storage_used_bytes = 9 * CHUNK
        await db_session.commit()

        response = await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        assert response.status_code == 413
        assert _chunk_objects(storage) == []

    async def test_file_over_the_size_limit_is_rejected_up_front(
        self, client, auth_headers, storage, monkeypatch
    ):
        monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 2 * 1024 * 1024)
        response = await _create(
            client, auth_headers, b"", total_size_bytes=2 * 1024 * 1024 + 1
        )
        assert response.status_code == 413
        assert response.json()["detail"] == (
            "File is too large. The limit is 2 MB per file."
        )

    async def test_unsupported_extension_is_rejected_up_front(
        self, client, auth_headers, storage
    ):
        response = await _create(
            client, auth_headers, _pdf_bytes(), filename="notes.docx"
        )
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Unsupported file type. Yarrow accepts PDF, PNG, JPEG and GIF files."
        )

    @pytest.mark.parametrize("chunk_size", [CHUNK - 1, 16 * 1024 * 1024 + 1])
    async def test_chunk_size_out_of_bounds_is_rejected(
        self, client, auth_headers, storage, chunk_size
    ):
        response = await _create(
            client, auth_headers, _pdf_bytes(), chunk_size_bytes=chunk_size
        )
        assert response.status_code == 400

    async def test_renamed_file_is_refused_on_its_first_chunk(
        self, client, auth_headers, storage, db_session
    ):
        """The contents decide the type (US-37), checked before the user
        waits for the rest of the transfer."""
        content = b"PK\x03\x04" + os.urandom(CHUNK * 2)  # a zip, named .pdf
        session_id = await _started(client, auth_headers, content)
        response = await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Unsupported file type.")

        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["status"] == "failed"
        assert state["error_message"].startswith("Unsupported file type.")
        assert _chunk_objects(storage) == []
        more = await _put(client, auth_headers, session_id, 1, _chunks(content)[1])
        assert more.status_code == 400

    async def test_assembled_file_with_wrong_checksum_is_rejected(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(
            client, auth_headers, content, sha256=_sha(b"something else")
        )
        await _send_all(client, auth_headers, session_id, content)

        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert response.status_code == 400
        assert "checksum" in response.json()["detail"]
        assert len(fake_queue) == 0
        assert (await db_session.scalar(select(func.count()).select_from(Job))) == 0
        assert _chunk_objects(storage) == []
        user = await _user(db_session, auth_headers, client)
        assert (user.storage_used_bytes or 0) == 0

    async def test_assembled_file_with_matching_checksum_is_accepted(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        session_id = await _started(
            client, auth_headers, content, sha256=_sha(content).upper()
        )
        await _send_all(client, auth_headers, session_id, content)
        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert response.status_code == 200, response.text

    async def test_assembled_contents_are_sniffed_again_at_finalize(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """Even if the first chunk's stored bytes change behind our back,
        finalize trusts only what it assembles."""
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        first = min(_chunk_objects(storage))
        storage.objects[first] = b"\x00" * len(storage.objects[first])

        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Unsupported file type.")
        assert len(fake_queue) == 0


# ---------------------------------------------------------------------------
# Cleanup of aborted and abandoned uploads.
# ---------------------------------------------------------------------------
class TestCleanup:
    async def test_abort_deletes_the_session_and_its_chunks(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])

        response = await client.delete(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert response.status_code == 204
        assert _chunk_objects(storage) == []
        assert await db_session.get(UploadSession, uuid.UUID(session_id)) is None
        gone = await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert gone.status_code == 404

    async def test_aborting_a_finished_upload_keeps_its_document(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        await client.delete(f"{UPLOADS}/{session_id}", headers=auth_headers)
        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert document is not None
        assert storage.objects[document.storage_key] == content

    async def test_expired_sessions_are_swept_with_their_chunks(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        stale = await _started(client, auth_headers, content)
        fresh = await _started(client, auth_headers, content, filename="fresh.pdf")
        await _put(client, auth_headers, stale, 0, _chunks(content)[0])
        await _put(client, auth_headers, fresh, 0, _chunks(content)[0])
        stale_row = await db_session.get(UploadSession, uuid.UUID(stale))
        stale_row.expires_at = utcnow() - timedelta(minutes=1)
        await db_session.commit()

        # Swept opportunistically whenever the document list is read.
        listed = await client.get("/api/v1/documents/", headers=auth_headers)
        assert listed.status_code == 200

        assert await db_session.get(UploadSession, uuid.UUID(stale)) is None
        assert await db_session.get(UploadSession, uuid.UUID(fresh)) is not None
        remaining = _chunk_objects(storage)
        assert len(remaining) == 1
        assert remaining[0].startswith(f"uploads/{fresh}/")

    async def test_sweep_keeps_a_session_whose_chunks_could_not_be_deleted(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])
        row = await db_session.get(UploadSession, uuid.UUID(session_id))
        row.expires_at = utcnow() - timedelta(minutes=1)
        await db_session.commit()

        def broken(key):
            raise RuntimeError("storage down")

        storage.delete_file = broken
        assert await purge_expired_upload_sessions(db_session, lambda: storage) == 0
        assert await db_session.get(UploadSession, uuid.UUID(session_id)) is not None

        del storage.delete_file  # storage is back
        assert await purge_expired_upload_sessions(db_session, lambda: storage) == 1
        assert _chunk_objects(storage) == []

    async def test_sweep_with_nothing_expired_touches_no_storage(self, db_session):
        def no_storage():
            raise AssertionError("storage should not be needed")

        assert await purge_expired_upload_sessions(db_session, no_storage) == 0

    async def test_deleting_the_account_removes_unfinished_uploads(
        self, client, auth_headers, storage, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 0, _chunks(content)[0])

        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=auth_headers,
            json={"password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 204, response.text
        assert _chunk_objects(storage) == []
        assert await db_session.get(UploadSession, uuid.UUID(session_id)) is None


# ---------------------------------------------------------------------------
# Review follow-ups: resuming only with the same file, and into the right run.
# ---------------------------------------------------------------------------
class TestSameFileAndRun:
    async def test_inspect_reports_each_confirmed_chunks_checksum(
        self, client, auth_headers, storage
    ):
        """What the browser compares its local file with before resuming."""
        content = _pdf_bytes()
        parts = _chunks(content)
        session_id = await _started(client, auth_headers, content)
        await _put(client, auth_headers, session_id, 2, parts[2])
        await _put(client, auth_headers, session_id, 0, parts[0])

        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["received_chunks"] == [0, 2]
        assert state["received_checksums"] == [_sha(parts[0]), _sha(parts[2])]
        assert state["file_sha256"] is None

    async def test_finalize_refuses_chunks_of_a_different_file(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """Two files that share name, size and timestamp: chunks of one were
        confirmed, then the other file sent the rest. The whole-file hash
        sent with finalize stops a document mixing both."""
        first = _pdf_bytes()
        second = first[:CHUNK] + os.urandom(len(first) - CHUNK)
        session_id = await _started(client, auth_headers, first)
        parts_first, parts_second = _chunks(first), _chunks(second)
        await _put(client, auth_headers, session_id, 0, parts_first[0])
        await _put(client, auth_headers, session_id, 1, parts_first[1])
        for index in (2, 3):
            await _put(client, auth_headers, session_id, index, parts_second[index])

        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"sha256": _sha(second)},
        )
        assert response.status_code == 400
        assert "checksum" in response.json()["detail"]
        assert len(fake_queue) == 0
        assert (await db_session.scalar(select(func.count()).select_from(Job))) == 0

    async def test_finalize_with_the_matching_file_hash_is_accepted(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"sha256": _sha(content)},
        )
        assert response.status_code == 200, response.text
        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["file_sha256"] == _sha(content)

    async def test_repeat_finalize_from_a_different_file_is_refused(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """A lost finalize reply, then a different file under the same
        fingerprint: it must not be told it is the stored document."""
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        url = f"{UPLOADS}/{session_id}/finalize"
        first = await client.post(
            url, headers=auth_headers, json={"sha256": _sha(content)}
        )
        assert first.status_code == 200

        other = await client.post(
            url, headers=auth_headers, json={"sha256": _sha(b"another file")}
        )
        assert other.status_code == 409
        assert other.json()["detail"]["code"] == "UPLOAD_FILE_MISMATCH"
        same = await client.post(
            url, headers=auth_headers, json={"sha256": _sha(content)}
        )
        assert same.json()["document_id"] == first.json()["document_id"]
        assert len(fake_queue) == 1

    async def test_upload_resumed_in_a_later_run_joins_that_run(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """US-31 after a reload: the session was started in run A, the page
        now shows run B, and finalize names B. B counts the document and A,
        which never stored a file, has no batch at all."""
        run_a, run_b = uuid.uuid4(), uuid.uuid4()
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content, batch_id=str(run_a))
        await _send_all(client, auth_headers, session_id, content)

        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"batch_id": str(run_b)},
        )
        assert done.status_code == 200, done.text
        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert document.batch_id == run_b
        progress = await client.get(
            f"/api/v1/documents/batches/{run_b}", headers=auth_headers
        )
        assert progress.json()["total"] == 1
        assert await db_session.get(Batch, run_a) is None
        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["batch_id"] == str(run_b)

    async def test_repeat_finalize_keeps_a_counted_document_in_its_run(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        """Moving a document already counted in a run would make that run's
        totals go down; like a retried single upload, it stays."""
        run_a, run_b = uuid.uuid4(), uuid.uuid4()
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content, batch_id=str(run_a))
        await _send_all(client, auth_headers, session_id, content)
        url = f"{UPLOADS}/{session_id}/finalize"
        first = await client.post(url, headers=auth_headers)
        again = await client.post(
            url, headers=auth_headers, json={"batch_id": str(run_b)}
        )
        assert again.json()["document_id"] == first.json()["document_id"]
        document = await db_session.get(
            Document, uuid.UUID(first.json()["document_id"])
        )
        assert document.batch_id == run_a
        assert await db_session.get(Batch, run_b) is None

    async def test_repeat_finalize_attaches_an_unbatched_document_to_the_run(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        url = f"{UPLOADS}/{session_id}/finalize"
        await client.post(url, headers=auth_headers)
        run = uuid.uuid4()
        again = await client.post(
            url, headers=auth_headers, json={"batch_id": str(run)}
        )
        document = await db_session.get(
            Document, uuid.UUID(again.json()["document_id"])
        )
        assert document.batch_id == run

    async def test_finalize_cannot_move_into_someone_elses_batch(
        self, client, auth_headers, storage, fake_queue, register, verify, login
    ):
        other = await _other_user(register, verify, login)
        theirs = uuid.uuid4()
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        await client.post(
            "/api/v1/documents/upload",
            headers=other,
            data={"batch_id": str(theirs)},
            files=[("files", ("theirs.pdf", pdf, "application/pdf"))],
        )
        content = _pdf_bytes()
        session_id = await _started(client, auth_headers, content)
        await _send_all(client, auth_headers, session_id, content)
        response = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"batch_id": str(theirs)},
        )
        assert response.status_code == 404
        assert len(fake_queue) == 1  # only the other user's upload


# ---------------------------------------------------------------------------
# US-79 x US-80: a large file is checked for duplicates when it is finalized,
# with the same rule as the single-request upload.
# ---------------------------------------------------------------------------
async def _single_upload(client, headers, content: bytes, name="small.pdf", **data):
    return await client.post(
        "/api/v1/documents/upload",
        headers=headers,
        data=data,
        files=[("files", (name, content, "application/pdf"))],
    )


async def _document_count(db_session, owner_id) -> int:
    return await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.owner_id == owner_id)
    )


class TestDuplicatesAtFinalize:
    async def _finished_chunks(self, client, headers, content, **extra) -> str:
        session_id = await _started(client, headers, content, **extra)
        await _send_all(client, headers, session_id, content)
        return session_id

    async def test_finalize_records_the_fingerprint(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        session_id = await self._finished_chunks(client, auth_headers, content)
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text
        document = await db_session.get(Document, uuid.UUID(done.json()["document_id"]))
        assert document.content_sha256 == _sha(content)

    async def test_identical_file_is_refused_with_the_duplicate_warning(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        first = await _single_upload(client, auth_headers, content, name="Lease.pdf")
        assert first.status_code == 200, first.text
        existing_id = first.json()["accepted"][0]["document_id"]
        user = await _user(db_session, auth_headers, client)
        used_before = user.storage_used_bytes

        session_id = await self._finished_chunks(
            client, auth_headers, content, filename="lease-copy.pdf"
        )
        refused = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"sha256": _sha(content)},
        )
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "UPLOAD_DUPLICATE"
        # The same shape as the single-request upload's UploadRejected.
        assert detail["filename"] == "lease-copy.pdf"
        assert detail["retryable"] is False
        assert detail["reason"] == (
            "You already have an identical document in your library: Lease.pdf."
        )
        assert detail["detail"] == detail["reason"]
        assert detail["duplicate_of"] == {
            "document_id": existing_id,
            "filename": "Lease.pdf",
            "status": "queued",
        }

        # Nothing stored, queued or counted, and no stray document object.
        db_session.expire_all()
        user = await _user(db_session, auth_headers, client)
        assert user.storage_used_bytes == used_before
        assert await _document_count(db_session, user.id) == 1
        assert len(fake_queue) == 1
        assert [k for k in storage.objects if k.startswith("documents/")] == [
            f"documents/{existing_id}/original"
        ]
        # The session stays open with every chunk, ready for Upload anyway.
        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["status"] == "processing"
        assert state["received_chunks"] == [0, 1, 2, 3]
        assert len(_chunk_objects(storage)) == 4

    async def test_duplicate_is_checked_before_the_file_is_stored(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        session_id = await self._finished_chunks(client, auth_headers, content)
        writes_before = dict(storage.writes)
        refused = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert refused.status_code == 409
        # No assembled document object was written and then thrown away.
        assert storage.writes == writes_before

    async def test_upload_anyway_finalizes_the_same_chunks(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        run = uuid.uuid4()
        session_id = await self._finished_chunks(
            client, auth_headers, content, batch_id=str(run)
        )
        url = f"{UPLOADS}/{session_id}/finalize"
        body = {"sha256": _sha(content), "batch_id": str(run)}
        refused = await client.post(url, headers=auth_headers, json=body)
        assert refused.status_code == 409

        copy = await client.post(
            url, headers=auth_headers, json={**body, "allow_duplicate": True}
        )
        assert copy.status_code == 200, copy.text
        document = await db_session.get(Document, uuid.UUID(copy.json()["document_id"]))
        assert storage.objects[document.storage_key] == content
        assert document.content_sha256 == _sha(content)
        assert document.batch_id == run
        # Every chunk was sent once: Upload anyway resent nothing.
        chunk_writes = [n for k, n in storage.writes.items() if "/chunks/" in k]
        assert chunk_writes == [1, 1, 1, 1]
        user = await _user(db_session, auth_headers, client)
        assert user.storage_used_bytes == 2 * len(content)
        assert await _document_count(db_session, user.id) == 2
        assert len(fake_queue) == 2

        # Still idempotent: finalizing again returns the same copy.
        again = await client.post(url, headers=auth_headers, json=body)
        assert again.status_code == 200
        assert again.json()["document_id"] == copy.json()["document_id"]
        assert len(fake_queue) == 2

    async def test_upload_anyway_is_still_subject_to_the_quota(
        self, client, auth_headers, storage, fake_queue, monkeypatch
    ):
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        session_id = await self._finished_chunks(client, auth_headers, content)
        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", len(content) + 10)
        copy = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"allow_duplicate": True},
        )
        assert copy.status_code == 413
        assert len(fake_queue) == 1

    async def test_skip_discards_the_session(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        session_id = await self._finished_chunks(client, auth_headers, content)
        await client.post(f"{UPLOADS}/{session_id}/finalize", headers=auth_headers)

        skipped = await client.delete(f"{UPLOADS}/{session_id}", headers=auth_headers)
        assert skipped.status_code == 204
        assert _chunk_objects(storage) == []
        assert await db_session.get(UploadSession, uuid.UUID(session_id)) is None
        user = await _user(db_session, auth_headers, client)
        assert await _document_count(db_session, user.id) == 1
        assert user.storage_used_bytes == len(content)

    async def test_duplicate_creates_no_batch(
        self, client, auth_headers, storage, fake_queue, db_session
    ):
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        run = uuid.uuid4()
        session_id = await self._finished_chunks(client, auth_headers, content)
        refused = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"batch_id": str(run)},
        )
        assert refused.status_code == 409
        assert await db_session.get(Batch, run) is None

    async def test_a_large_document_is_found_by_later_uploads(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        session_id = await self._finished_chunks(client, auth_headers, content)
        await client.post(f"{UPLOADS}/{session_id}/finalize", headers=auth_headers)

        single = await _single_upload(client, auth_headers, content)
        assert single.status_code == 400
        assert single.json()["detail"][0]["duplicate_of"]["filename"] == "scan.pdf"

        second = await self._finished_chunks(client, auth_headers, content)
        refused = await client.post(
            f"{UPLOADS}/{second}/finalize", headers=auth_headers
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["duplicate_of"]["filename"] == "scan.pdf"

    async def test_trashed_document_is_not_a_duplicate(
        self, client, auth_headers, storage, fake_queue
    ):
        content = _pdf_bytes()
        first = await _single_upload(client, auth_headers, content)
        existing_id = first.json()["accepted"][0]["document_id"]
        trashed = await client.delete(
            f"/api/v1/documents/{existing_id}", headers=auth_headers
        )
        assert trashed.status_code == 204, trashed.text

        session_id = await self._finished_chunks(client, auth_headers, content)
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text

    async def test_another_users_identical_file_is_not_a_duplicate(
        self, client, auth_headers, storage, fake_queue, register, verify, login
    ):
        content = _pdf_bytes()
        other = await _other_user(register, verify, login)
        assert (await _single_upload(client, other, content)).status_code == 200

        session_id = await self._finished_chunks(client, auth_headers, content)
        done = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert done.status_code == 200, done.text

    async def test_repeat_finalize_of_a_finished_upload_is_not_a_duplicate(
        self, client, auth_headers, storage, fake_queue
    ):
        """Idempotency first: a lost finalize reply gets its own document
        back, even if an identical copy was stored in the meantime."""
        content = _pdf_bytes()
        session_id = await self._finished_chunks(client, auth_headers, content)
        url = f"{UPLOADS}/{session_id}/finalize"
        first = await client.post(url, headers=auth_headers)
        assert first.status_code == 200
        copy = await _single_upload(
            client, auth_headers, content, allow_duplicate="true"
        )
        assert copy.status_code == 200

        again = await client.post(
            url, headers=auth_headers, json={"sha256": _sha(content)}
        )
        assert again.status_code == 200, again.text
        assert again.json()["document_id"] == first.json()["document_id"]
        assert len(fake_queue) == 2

    async def test_a_lookalike_file_is_refused_as_a_mismatch_not_a_duplicate(
        self, client, auth_headers, storage, fake_queue
    ):
        """US-80's whole-file check runs before the duplicate check, so
        chunks of a different file are never reported as a duplicate."""
        content = _pdf_bytes()
        await _single_upload(client, auth_headers, content)
        session_id = await self._finished_chunks(client, auth_headers, content)
        refused = await client.post(
            f"{UPLOADS}/{session_id}/finalize",
            headers=auth_headers,
            json={"sha256": _sha(b"another file")},
        )
        assert refused.status_code == 400
        assert "checksum" in refused.json()["detail"]

    async def test_identical_upload_committed_during_finalize_is_caught(
        self, client, auth_headers, storage, fake_queue, db_session, monkeypatch
    ):
        """The authoritative check runs under the user lock: an identical
        upload that commits while this one is being stored is still found,
        and the object stored for this one is removed again."""
        from app.api.v1.endpoints import uploads

        content = _pdf_bytes()
        real = uploads._identical_document
        calls = []

        async def not_yet(db, owner_id, digest):
            calls.append(digest)
            if len(calls) == 1:
                return None  # the identical upload has not committed yet
            return await real(db, owner_id, digest)

        await _single_upload(client, auth_headers, content)
        session_id = await self._finished_chunks(client, auth_headers, content)
        monkeypatch.setattr(uploads, "_identical_document", not_yet)
        refused = await client.post(
            f"{UPLOADS}/{session_id}/finalize", headers=auth_headers
        )
        assert refused.status_code == 409
        assert len(calls) == 2
        user = await _user(db_session, auth_headers, client)
        assert await _document_count(db_session, user.id) == 1
        assert len([k for k in storage.objects if k.startswith("documents/")]) == 1
        state = (
            await client.get(f"{UPLOADS}/{session_id}", headers=auth_headers)
        ).json()
        assert state["status"] == "processing"
