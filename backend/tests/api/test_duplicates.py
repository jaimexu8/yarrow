"""US-79: an upload identical to a document already in the caller's library
is refused with a warning naming that document; the user can skip it or
explicitly upload another copy. Another user's identical file is never
matched or disclosed.

Acceptance criteria map to the test classes:

1. Warned before another copy is accepted -> TestWarning
2. Skip creates no document and uses no quota -> TestSkip
3. Upload Anyway stores a separate copy, normal size/quota rules -> TestUploadAnyway
4. Another owner's identical file is not disclosed -> TestOtherOwners

TestConcurrentUploads covers two simultaneous uploads of the same bytes.
"""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from yarrow_db.config import db_settings
from yarrow_db.models import Batch, Document, Job, User

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"
PDF = (FIXTURES_DIR / "sample.pdf").read_bytes()
# Same type, different bytes: anything after %%EOF is ignored by PDF readers.
OTHER_PDF = PDF + b"\n% a different file\n"


async def _upload(
    client,
    headers,
    name="sample.pdf",
    content=PDF,
    *,
    allow_duplicate=False,
    batch_id=None,
    upload_id=None,
):
    """One file per request, the way the Upload page sends them."""
    data = {}
    if allow_duplicate:
        data["allow_duplicate"] = "true"
    if batch_id is not None:
        data["batch_id"] = str(batch_id)
    if upload_id is not None:
        data["client_upload_ids"] = upload_id
    return await client.post(
        UPLOAD,
        headers=headers,
        data=data,
        files=[("files", (name, content, "application/pdf"))],
    )


async def _library(client, headers) -> list[dict]:
    response = await client.get(DOCUMENTS, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _storage_used(client, headers) -> int:
    me = await client.get("/api/v1/auth/me", headers=headers)
    return me.json()["storage_used_bytes"]


async def _other_user(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def _only_rejection(response) -> dict:
    """The single rejected file of an all-rejected (400) request."""
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert len(detail) == 1
    return detail[0]


class TestFingerprint:
    async def test_upload_stores_the_sha256_of_its_bytes(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        response = await _upload(client, auth_headers)
        document_id = uuid.UUID(response.json()["accepted"][0]["document_id"])

        document = await db_session.get(Document, document_id)
        assert document.content_sha256 == hashlib.sha256(PDF).hexdigest()

    async def test_fingerprint_is_not_exposed_by_the_api(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        response = await _upload(client, auth_headers)
        document_id = response.json()["accepted"][0]["document_id"]

        detail = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        assert "content_sha256" not in detail.json()
        assert hashlib.sha256(PDF).hexdigest() not in detail.text


class TestWarning:
    """Criterion 1: an identical file is refused with a warning first."""

    async def test_identical_file_is_refused_with_a_warning(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers)
        existing_id = first.json()["accepted"][0]["document_id"]

        again = await _upload(client, auth_headers)

        rejection = _only_rejection(again)
        assert rejection["filename"] == "sample.pdf"
        assert rejection["retryable"] is False
        assert "already have an identical document" in rejection["reason"]
        assert rejection["duplicate_of"] == {
            "document_id": existing_id,
            "filename": "sample.pdf",
            "status": "queued",
        }
        assert len(await _library(client, auth_headers)) == 1

    async def test_renamed_file_with_identical_bytes_is_a_duplicate(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers, name="invoice.pdf")
        existing_id = first.json()["accepted"][0]["document_id"]

        again = await _upload(client, auth_headers, name="invoice (copy).pdf")

        rejection = _only_rejection(again)
        assert rejection["filename"] == "invoice (copy).pdf"
        assert rejection["duplicate_of"]["document_id"] == existing_id
        assert rejection["duplicate_of"]["filename"] == "invoice.pdf"

    async def test_warning_names_the_documents_current_name(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers, name="scan.pdf")
        existing_id = first.json()["accepted"][0]["document_id"]
        renamed = await client.patch(
            f"{DOCUMENTS}{existing_id}",
            headers=auth_headers,
            json={"filename": "Lease 2026.pdf"},
        )
        assert renamed.status_code == 200, renamed.text

        again = await _upload(client, auth_headers, name="scan.pdf")

        assert _only_rejection(again)["duplicate_of"]["filename"] == "Lease 2026.pdf"

    async def test_different_bytes_are_not_a_duplicate(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        await _upload(client, auth_headers, name="sample.pdf")

        other = await _upload(
            client, auth_headers, name="sample.pdf", content=OTHER_PDF
        )

        assert other.status_code == 200, other.text
        assert other.json()["rejected"] == []
        assert len(await _library(client, auth_headers)) == 2

    async def test_two_identical_files_in_one_request(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        response = await client.post(
            UPLOAD,
            headers=auth_headers,
            files=[
                ("files", ("a.pdf", PDF, "application/pdf")),
                ("files", ("b.pdf", PDF, "application/pdf")),
            ],
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["filename"] for item in body["accepted"]] == ["a.pdf"]
        assert len(body["rejected"]) == 1
        assert body["rejected"][0]["filename"] == "b.pdf"
        assert body["rejected"][0]["duplicate_of"] == {
            "document_id": body["accepted"][0]["document_id"],
            "filename": "a.pdf",
            "status": "queued",
        }
        assert len(fake_storage.objects) == 1

    async def test_failed_document_still_counts_and_its_status_is_reported(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        first = await _upload(client, auth_headers)
        existing_id = uuid.UUID(first.json()["accepted"][0]["document_id"])
        document = await db_session.get(Document, existing_id)
        document.status = "failed"
        await db_session.commit()

        again = await _upload(client, auth_headers)

        assert _only_rejection(again)["duplicate_of"]["status"] == "failed"

    async def test_trashed_document_is_not_a_duplicate(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers)
        existing_id = first.json()["accepted"][0]["document_id"]
        trashed = await client.delete(f"{DOCUMENTS}{existing_id}", headers=auth_headers)
        assert trashed.status_code == 204, trashed.text

        again = await _upload(client, auth_headers)

        assert again.status_code == 200, again.text
        assert again.json()["accepted"][0]["document_id"] != existing_id

    async def test_documents_without_a_fingerprint_never_match(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """Documents uploaded before migration 0017 have no fingerprint."""
        first = await _upload(client, auth_headers)
        document = await db_session.get(
            Document, uuid.UUID(first.json()["accepted"][0]["document_id"])
        )
        document.content_sha256 = None
        await db_session.commit()

        again = await _upload(client, auth_headers)

        assert again.status_code == 200, again.text

    async def test_retry_of_a_stored_upload_is_not_a_duplicate(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Same client_upload_id: the earlier result, not a warning."""
        first = await _upload(client, auth_headers, upload_id="upload-1")
        retry = await _upload(client, auth_headers, upload_id="upload-1")

        assert retry.status_code == 200, retry.text
        assert retry.json()["rejected"] == []
        assert (
            retry.json()["accepted"][0]["document_id"]
            == first.json()["accepted"][0]["document_id"]
        )
        assert len(fake_storage.objects) == 1


class TestSkip:
    """Criterion 2: after the warning nothing was stored, so skipping costs
    nothing. (Skip itself sends no request; see the Upload page tests.)"""

    async def test_skipped_duplicate_creates_no_document_and_uses_no_quota(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _upload(client, auth_headers)
        used_before = await _storage_used(client, auth_headers)

        again = await _upload(client, auth_headers)

        assert again.status_code == 400
        assert await _storage_used(client, auth_headers) == used_before == len(PDF)
        assert len(await _library(client, auth_headers)) == 1
        assert len(fake_storage.objects) == 1
        assert len(fake_queue) == 1
        jobs = await db_session.scalar(select(func.count()).select_from(Job))
        assert jobs == 1

    async def test_skipped_duplicate_creates_no_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _upload(client, auth_headers)
        batch_id = uuid.uuid4()

        again = await _upload(client, auth_headers, batch_id=batch_id)

        assert again.status_code == 400
        assert await db_session.get(Batch, batch_id) is None
        progress = await client.get(
            f"{DOCUMENTS}batches/{batch_id}", headers=auth_headers
        )
        assert progress.status_code == 404


class TestUploadAnyway:
    """Criterion 3: allow_duplicate stores a separate copy, normal rules."""

    async def test_upload_anyway_stores_a_separate_document(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        first = await _upload(client, auth_headers)
        existing_id = first.json()["accepted"][0]["document_id"]
        await _upload(client, auth_headers)  # the warning

        anyway = await _upload(client, auth_headers, allow_duplicate=True)

        assert anyway.status_code == 200, anyway.text
        copy_id = anyway.json()["accepted"][0]["document_id"]
        assert copy_id != existing_id
        assert len(await _library(client, auth_headers)) == 2
        assert len(fake_storage.objects) == 2
        assert len(fake_queue) == 2
        assert await _storage_used(client, auth_headers) == 2 * len(PDF)
        copy = await db_session.get(Document, uuid.UUID(copy_id))
        assert copy.content_sha256 == hashlib.sha256(PDF).hexdigest()

    async def test_upload_anyway_is_subject_to_the_quota(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", len(PDF) + 10)
        await _upload(client, auth_headers)

        anyway = await _upload(client, auth_headers, allow_duplicate=True)

        rejection = _only_rejection(anyway)
        assert "storage" in rejection["reason"].lower()
        assert rejection["duplicate_of"] is None
        assert len(fake_storage.objects) == 1

    async def test_upload_anyway_is_subject_to_the_size_limit(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        from app.core.config import settings

        await _upload(client, auth_headers)
        monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", len(PDF) - 1)

        anyway = await _upload(client, auth_headers, allow_duplicate=True)

        rejection = _only_rejection(anyway)
        assert "too large" in rejection["reason"]
        assert len(fake_storage.objects) == 1

    async def test_upload_anyway_joins_the_batch_of_its_run(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _upload(client, auth_headers, name="old.pdf")
        batch_id = uuid.uuid4()
        fresh = await _upload(
            client, auth_headers, name="new.pdf", content=OTHER_PDF, batch_id=batch_id
        )
        assert fresh.status_code == 200, fresh.text
        warned = await _upload(
            client, auth_headers, batch_id=batch_id, upload_id="file-dup"
        )
        assert warned.status_code == 400

        anyway = await _upload(
            client,
            auth_headers,
            batch_id=batch_id,
            upload_id="file-dup",
            allow_duplicate=True,
        )

        assert anyway.status_code == 200, anyway.text
        copy = await db_session.get(
            Document, uuid.UUID(anyway.json()["accepted"][0]["document_id"])
        )
        assert copy.batch_id == batch_id
        progress = await client.get(
            f"{DOCUMENTS}batches/{batch_id}", headers=auth_headers
        )
        assert progress.json()["total"] == 2

    async def test_retried_upload_anyway_is_not_stored_twice(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        await _upload(client, auth_headers)
        first = await _upload(
            client, auth_headers, allow_duplicate=True, upload_id="copy-1"
        )
        retry = await _upload(
            client, auth_headers, allow_duplicate=True, upload_id="copy-1"
        )

        assert (
            retry.json()["accepted"][0]["document_id"]
            == first.json()["accepted"][0]["document_id"]
        )
        assert len(fake_storage.objects) == 2


class TestOtherOwners:
    """Criterion 4: nothing about another user's identical file leaks."""

    async def test_another_users_identical_file_is_not_a_duplicate(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        theirs = await _upload(client, auth_headers, name="their-private-name.pdf")
        their_id = theirs.json()["accepted"][0]["document_id"]
        mine_headers = await _other_user(register, verify, login)

        mine = await _upload(client, mine_headers, name="mine.pdf")

        assert mine.status_code == 200, mine.text
        assert mine.json()["rejected"] == []
        assert "their-private-name" not in mine.text
        assert their_id not in mine.text

    async def test_warning_only_ever_names_my_own_document(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        theirs = await _upload(client, auth_headers, name="their-private-name.pdf")
        their_id = theirs.json()["accepted"][0]["document_id"]
        mine_headers = await _other_user(register, verify, login)
        mine = await _upload(client, mine_headers, name="mine.pdf")
        my_id = mine.json()["accepted"][0]["document_id"]

        again = await _upload(client, mine_headers, name="again.pdf")

        rejection = _only_rejection(again)
        assert rejection["duplicate_of"]["document_id"] == my_id
        assert rejection["duplicate_of"]["filename"] == "mine.pdf"
        assert "their-private-name" not in again.text
        assert their_id not in again.text


# ---------------------------------------------------------------------------
# Concurrency. The shared ``client`` fixture runs every request in one
# rolled-back session, which cannot show two requests racing; these tests use
# real sessions and real commits, and clean up after themselves.
# ---------------------------------------------------------------------------
@pytest.fixture
async def committing_app() -> AsyncGenerator[tuple[AsyncClient, dict, uuid.UUID]]:
    """A client whose requests each get their own committing session, and a
    user created for this test only (removed again afterwards)."""
    engine = create_async_engine(db_settings.async_url, poolclass=NullPool)
    make_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with make_session() as session:
        session.add(
            User(
                id=user_id,
                email=f"race-{user_id.hex[:12]}@example.com",
                hashed_password="not-used",
                is_verified=True,
                session_version=0,
                storage_used_bytes=0,
            )
        )
        await session.commit()

    async def own_session() -> AsyncGenerator[AsyncSession]:
        async with make_session() as session:
            yield session

    app.dependency_overrides[get_db] = own_session
    token = create_access_token({"sub": str(user_id), "sv": 0})
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, headers, user_id
    finally:
        app.dependency_overrides.clear()
        async with make_session() as session:
            documents = select(Document.id).where(Document.owner_id == user_id)
            await session.execute(delete(Job).where(Job.document_id.in_(documents)))
            await session.execute(delete(Document).where(Document.owner_id == user_id))
            await session.execute(delete(Batch).where(Batch.owner_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


@pytest.fixture
def slow_first_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pause the first file measured, and the first duplicate lookup, after
    they have run and before anything is committed. Without the user row lock
    the other request would run its own checks during that pause, see nothing
    committed yet, and store a second copy (or lose a quota update); with the
    lock it has to wait until the first request commits."""
    from app.api.v1.endpoints import documents

    def pause_first_call(original):
        calls = 0

        async def wrapper(*args):
            nonlocal calls
            calls += 1
            result = await original(*args)
            if calls == 1:
                await asyncio.sleep(0.3)
            return result

        return wrapper

    monkeypatch.setattr(documents, "_measure", pause_first_call(documents._measure))
    monkeypatch.setattr(
        documents,
        "_identical_document",
        pause_first_call(documents._identical_document),
    )


class TestConcurrentUploads:
    async def test_simultaneous_identical_uploads_keep_one_copy(
        self, committing_app, fake_storage, fake_queue, slow_first_checks
    ):
        """The user row lock serialises the two requests: whichever commits
        first is stored, the other sees it and gets the duplicate warning."""
        http, headers, _user_id = committing_app

        responses = await asyncio.gather(
            _upload(http, headers, name="a.pdf", upload_id="a"),
            _upload(http, headers, name="b.pdf", upload_id="b"),
        )

        codes = sorted(response.status_code for response in responses)
        assert codes == [200, 400], [response.text for response in responses]
        stored = next(r for r in responses if r.status_code == 200)
        warned = next(r for r in responses if r.status_code == 400)
        stored_doc = stored.json()["accepted"][0]
        assert warned.json()["detail"][0]["duplicate_of"] == {
            "document_id": stored_doc["document_id"],
            "filename": stored_doc["filename"],
            "status": "queued",
        }
        assert len(fake_storage.objects) == 1
        me = await http.get("/api/v1/auth/me", headers=headers)
        assert me.json()["storage_used_bytes"] == len(PDF)

    async def test_simultaneous_upload_anyway_keeps_both_copies(
        self, committing_app, fake_storage, fake_queue, slow_first_checks
    ):
        http, headers, _user_id = committing_app

        responses = await asyncio.gather(
            _upload(http, headers, name="a.pdf", allow_duplicate=True),
            _upload(http, headers, name="b.pdf", allow_duplicate=True),
        )

        assert [response.status_code for response in responses] == [200, 200]
        assert len(fake_storage.objects) == 2
        me = await http.get("/api/v1/auth/me", headers=headers)
        # Both counted: the lock also keeps the quota total right.
        assert me.json()["storage_used_bytes"] == 2 * len(PDF)
