"""Test soft-deleting a document, restoring it, and purging after 7 days"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from yarrow_db.models import (
    Document,
    DocumentShare,
    Job,
    JobPage,
    Page,
    Region,
    RegionImage,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    User,
    Warning,
)

from app.services.document_deletion import (
    TRASH_RETENTION_DAYS,
    DocumentDeletionError,
    DocumentDeletionOutcome,
)
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"
TRASH = "/api/v1/documents/trash"


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records revoked task ids instead of talking to Celery."""
    from app.api.v1.endpoints import documents

    calls: list[str] = []
    monkeypatch.setattr(documents, "revoke_document_processing", calls.append)
    return calls


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        # The same fixture bytes are uploaded many times; not a duplicate test.
        data={"allow_duplicate": "true"},
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _other_user_headers(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _set_status(db_session, job_id, status: str) -> None:
    """What the worker writes as it picks up or finishes a job."""
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = status
    await db_session.flush()


async def _add_extracted_data(db_session, document_id, share_with) -> str:
    """One page holding a text region, an image region and a one-cell table,
    plus a warning and a share: every kind of row that hangs off a document.
    Returns the extracted image's storage key."""
    page = Page(id=uuid.uuid4(), document_id=document_id, page_number=1)
    text, image, table_region = (
        Region(id=uuid.uuid4(), page_id=page.id, reading_order=order)
        for order in range(3)
    )
    table = Table(id=uuid.uuid4(), document_id=document_id, row_count=1, col_count=1)
    region_table = RegionTable(
        id=uuid.uuid4(),
        region_id=table_region.id,
        table_id=table.id,
        row_start=0,
        row_end=0,
        col_start=0,
        col_end=0,
    )
    image_key = f"images/{document_id}/figure-1.png"
    db_session.add_all(
        [
            page,
            text,
            image,
            table_region,
            RegionText(region_id=text.id, text_content="Quarterly summary"),
            RegionImage(region_id=image.id, image_key=image_key),
            table,
            region_table,
            TableCell(
                region_table_id=region_table.id, row_idx=0, col_idx=0, text_content="42"
            ),
            Warning(page_id=page.id, warning_type="blur", message="Page is blurry"),
            DocumentShare(
                document_id=document_id,
                shared_with_user_id=share_with,
                permission="view",
            ),
        ]
    )
    await db_session.flush()
    return image_key


async def _count(db_session, model, *where) -> int:
    return await db_session.scalar(
        select(func.count()).select_from(model).where(*where)
    )


async def _expire_trash(db_session, document_id, days=TRASH_RETENTION_DAYS + 1):
    document = await db_session.get(Document, document_id)
    document.deleted_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    await db_session.flush()


class TestSoftDeleteOwnDocument:
    async def test_document_is_removed_from_the_library(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        """Ensure that a deleted document is removed from the user's library."""
        document_id, _ = await _upload(client, auth_headers)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert response.content == b""
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library == []
        detail = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        assert detail.status_code == 404
        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert [doc["id"] for doc in trash] == [str(document_id)]
        assert trash[0]["deleted_at"] is not None

    async def test_extracted_data_and_files_are_kept(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        """Soft delete keeps extracted rows and stored files so restore works."""
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        document = await db_session.get(Document, document_id)
        image_key = await _add_extracted_data(
            db_session, document_id, share_with=document.owner_id
        )
        fake_storage.objects[image_key] = b"png"
        stored = {document.storage_key, image_key}
        assert stored <= fake_storage.objects.keys()

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert await _count(db_session, Document, Document.id == document_id) == 1
        assert await _count(db_session, Job, Job.document_id == document_id) == 1
        assert await _count(db_session, Page, Page.document_id == document_id) == 1
        assert await _count(db_session, Table, Table.document_id == document_id) == 1
        assert (
            await _count(
                db_session, DocumentShare, DocumentShare.document_id == document_id
            )
            == 1
        )
        for model in (Region, RegionText, RegionImage, RegionTable, TableCell, Warning):
            assert await _count(db_session, model) >= 1, model.__name__
        assert stored <= fake_storage.objects.keys()

    async def test_storage_quota_is_not_refunded(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        """Quota stays held until permanent purge, so restore cannot fail it."""
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        owner = await db_session.get(User, document.owner_id)
        await db_session.refresh(owner)
        used_before = owner.storage_used_bytes
        assert used_before >= document.file_size_bytes > 0

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before

    async def test_other_documents_are_untouched(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        """Ensure that deleting one document does not affect others"""
        keep_id, _ = await _upload(client, auth_headers)
        delete_id, _ = await _upload(client, auth_headers)

        await client.delete(f"{DOCUMENTS}{delete_id}", headers=auth_headers)

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(keep_id)]


class TestDeleteInAnyState:
    async def test_queued_document_is_deleted_and_its_task_revoked(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        task_id = (await db_session.get(Job, job_id)).celery_task_id

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert revoked == [task_id] == ["task-1"]
        job = await db_session.get(Job, job_id)
        document = await db_session.get(Document, document_id)
        assert job.status == "canceled"
        assert document.status == "canceled"
        assert document.deleted_at is not None

    async def test_deleting_a_running_document_cancels_its_open_pages(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        job = await db_session.get(Job, job_id)
        job.status = "processing"
        db_session.add_all(
            [
                JobPage(job_id=job_id, page_number=1, status="completed"),
                JobPage(job_id=job_id, page_number=2, status="processing"),
            ]
        )
        await db_session.flush()

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        await db_session.refresh(job)
        assert job.status == "canceled"
        assert job.current_stage == "canceled"
        statuses = (
            await db_session.execute(
                select(JobPage.status)
                .where(JobPage.job_id == job_id)
                .order_by(JobPage.page_number)
            )
        ).scalars().all()
        assert statuses == ["completed", "canceled"]

    @pytest.mark.parametrize("status", ["processing", "failed", "canceled"])
    async def test_document_in_other_states_is_deleted(
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

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        # Only a job that might still be waiting in the queue is revoked.
        assert revoked == (["task-1"] if status == "processing" else [])


class TestFailuresOutsideTheDatabase:
    async def test_revoke_failure_does_not_fail_the_delete(
        self, client, auth_headers, fake_storage, fake_queue, monkeypatch
    ):
        from app.api.v1.endpoints import documents

        def _broker_down(task_id):
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(documents, "revoke_document_processing", _broker_down)
        document_id, _ = await _upload(client, auth_headers)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 204
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library == []

    async def test_busy_document_is_a_conflict(
        self, client, auth_headers, fake_storage, fake_queue, revoked, monkeypatch
    ):
        """While processing holds the document's row lock, the request gives
        up after a short wait with a retryable 409 and deletes nothing."""
        from app.api.v1.endpoints import documents

        async def _busy(db, document_id):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.BUSY, detail="try again shortly"
            )

        monkeypatch.setattr(documents, "soft_delete_document", _busy)
        document_id, _ = await _upload(client, auth_headers)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DOCUMENT_BUSY"
        assert revoked == []
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(document_id)]


class TestAccess:
    async def test_another_users_document_cannot_be_deleted(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        register,
        verify,
        login,
    ):
        document_id, _ = await _upload(client, auth_headers)
        other = await _other_user_headers(register, verify, login)

        response = await client.delete(f"{DOCUMENTS}{document_id}", headers=other)

        # Indistinguishable from a document that does not exist.
        assert response.status_code == 404
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(document_id)]

    async def test_missing_document_is_not_found(self, client, auth_headers):
        response = await client.delete(
            f"{DOCUMENTS}{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_deleting_twice_is_not_found(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        document_id, _ = await _upload(client, auth_headers)
        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}", headers=auth_headers
        )

        assert response.status_code == 404

    async def test_requires_sign_in(self, client):
        response = await client.delete(f"{DOCUMENTS}{uuid.uuid4()}")
        assert response.status_code == 401

    async def test_trashed_shared_document_is_hidden_from_recipient(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        db_session,
        register,
        verify,
        login,
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        other = await _other_user_headers(register, verify, login)
        other_me = (await client.get("/api/v1/auth/me", headers=other)).json()
        db_session.add(
            DocumentShare(
                document_id=document_id,
                shared_with_user_id=uuid.UUID(other_me["id"]),
                permission="view",
            )
        )
        await db_session.flush()

        before = (await client.get(DOCUMENTS, headers=other)).json()
        assert [doc["id"] for doc in before] == [str(document_id)]

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        after = (await client.get(DOCUMENTS, headers=other)).json()
        assert after == []
        detail = await client.get(f"{DOCUMENTS}{document_id}", headers=other)
        assert detail.status_code == 404


class TestRestore:
    async def test_restore_returns_the_document_and_its_extracted_data(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        document = await db_session.get(Document, document_id)
        image_key = await _add_extracted_data(
            db_session, document_id, share_with=document.owner_id
        )
        fake_storage.objects[image_key] = b"png"
        owner = await db_session.get(User, document.owner_id)
        await db_session.refresh(owner)
        used_before = owner.storage_used_bytes

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        response = await client.post(
            f"{DOCUMENTS}{document_id}/restore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(document_id)
        assert body["deleted_at"] is None
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(document_id)]
        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert trash == []
        detail = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        assert detail.status_code == 200
        assert len(detail.json()["pages"]) == 1
        assert await _count(db_session, Page, Page.document_id == document_id) == 1
        assert image_key in fake_storage.objects
        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before

    async def test_restore_of_missing_document_is_404(self, client, auth_headers):
        response = await client.post(
            f"{DOCUMENTS}{uuid.uuid4()}/restore", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_restore_of_a_library_document_is_400(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        document_id, _ = await _upload(client, auth_headers)
        response = await client.post(
            f"{DOCUMENTS}{document_id}/restore", headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NOT_IN_TRASH"

    async def test_another_user_cannot_restore(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        register,
        verify,
        login,
    ):
        document_id, _ = await _upload(client, auth_headers)
        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        other = await _other_user_headers(register, verify, login)

        response = await client.post(f"{DOCUMENTS}{document_id}/restore", headers=other)
        assert response.status_code == 404

    async def test_expired_restore_purges_and_is_gone(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        document = await db_session.get(Document, document_id)
        image_key = await _add_extracted_data(
            db_session, document_id, share_with=document.owner_id
        )
        fake_storage.objects[image_key] = b"png"
        stored = {document.storage_key, image_key}
        owner = await db_session.get(User, document.owner_id)
        await db_session.refresh(owner)
        used_before = owner.storage_used_bytes
        size = document.file_size_bytes

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        await _expire_trash(db_session, document_id)

        response = await client.post(
            f"{DOCUMENTS}{document_id}/restore", headers=auth_headers
        )

        assert response.status_code == 410, response.text
        assert response.json()["detail"]["code"] == "TRASH_EXPIRED"
        assert await _count(db_session, Document, Document.id == document_id) == 0
        for model, where in [
            (Job, Job.document_id == document_id),
            (Page, Page.document_id == document_id),
            (Table, Table.document_id == document_id),
            (DocumentShare, DocumentShare.document_id == document_id),
        ]:
            assert await _count(db_session, model, where) == 0, model.__name__
        for model in (Region, RegionText, RegionImage, RegionTable, TableCell, Warning):
            assert await _count(db_session, model) == 0, model.__name__
        assert not stored & fake_storage.objects.keys()
        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before - size


class TestTrashListAndPurge:
    async def test_trash_lists_only_this_users_deleted_documents(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        register,
        verify,
        login,
    ):
        mine, _ = await _upload(client, auth_headers)
        other = await _other_user_headers(register, verify, login)
        theirs, _ = await _upload(client, other)
        await client.delete(f"{DOCUMENTS}{mine}", headers=auth_headers)
        await client.delete(f"{DOCUMENTS}{theirs}", headers=other)

        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert [doc["id"] for doc in trash] == [str(mine)]

    async def test_listing_trash_purges_expired_documents(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        keep_id, _ = await _upload(client, auth_headers)
        expire_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        document = await db_session.get(Document, expire_id)
        image_key = await _add_extracted_data(
            db_session, expire_id, share_with=document.owner_id
        )
        fake_storage.objects[image_key] = b"png"
        stored = {document.storage_key, image_key}

        await client.delete(f"{DOCUMENTS}{keep_id}", headers=auth_headers)
        await client.delete(f"{DOCUMENTS}{expire_id}", headers=auth_headers)
        await _expire_trash(db_session, expire_id)

        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert [doc["id"] for doc in trash] == [str(keep_id)]
        assert await _count(db_session, Document, Document.id == expire_id) == 0
        assert not stored & fake_storage.objects.keys()

    async def test_listing_the_library_also_purges_expired_documents(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, _ = await _upload(client, auth_headers)
        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        await _expire_trash(db_session, document_id)

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library == []
        assert await _count(db_session, Document, Document.id == document_id) == 0

    async def test_retention_window_is_fixed_at_seven_days(self):
        assert TRASH_RETENTION_DAYS == 7


class TestPermanentDeleteFromTrash:
    async def test_permanent_delete_wipes_rows_files_and_quota(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _set_status(db_session, job_id, "completed")
        document = await db_session.get(Document, document_id)
        image_key = await _add_extracted_data(
            db_session, document_id, share_with=document.owner_id
        )
        fake_storage.objects[image_key] = b"png"
        stored = {document.storage_key, image_key}
        owner = await db_session.get(User, document.owner_id)
        await db_session.refresh(owner)
        used_before = owner.storage_used_bytes
        size = document.file_size_bytes

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        response = await client.delete(
            f"{DOCUMENTS}{document_id}/permanent", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert await _count(db_session, Document, Document.id == document_id) == 0
        assert not stored & fake_storage.objects.keys()
        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before - size

    async def test_permanent_delete_of_a_library_document_is_not_found(
        self, client, auth_headers, fake_storage, fake_queue, revoked
    ):
        document_id, _ = await _upload(client, auth_headers)
        response = await client.delete(
            f"{DOCUMENTS}{document_id}/permanent", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_quota_never_goes_negative(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        owner = await db_session.get(User, document.owner_id)
        owner.storage_used_bytes = 1
        await db_session.flush()

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        await client.delete(f"{DOCUMENTS}{document_id}/permanent", headers=auth_headers)

        await db_session.refresh(owner)
        assert owner.storage_used_bytes == 0

    async def test_storage_failure_does_not_fail_the_delete(
        self, client, auth_headers, fake_storage, fake_queue, revoked, monkeypatch
    ):
        """The rows are already committed, so the document is gone either way;
        the leftover file is logged for cleanup instead."""

        def _storage_down(key):
            raise ConnectionError("storage unavailable")

        document_id, _ = await _upload(client, auth_headers)
        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        monkeypatch.setattr(fake_storage, "delete_file", _storage_down)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}/permanent", headers=auth_headers
        )

        assert response.status_code == 204
        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert trash == []

    async def test_empty_trash_wipes_every_trashed_document(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        keep_id, _ = await _upload(client, auth_headers)
        first_id, _ = await _upload(client, auth_headers)
        second_id, _ = await _upload(client, auth_headers)
        await client.delete(f"{DOCUMENTS}{first_id}", headers=auth_headers)
        await client.delete(f"{DOCUMENTS}{second_id}", headers=auth_headers)

        response = await client.delete(TRASH, headers=auth_headers)

        assert response.status_code == 204, response.text
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(keep_id)]
        trash = (await client.get(TRASH, headers=auth_headers)).json()
        assert trash == []
        assert await _count(db_session, Document, Document.id == first_id) == 0
        assert await _count(db_session, Document, Document.id == second_id) == 0
        assert await _count(db_session, Document, Document.id == keep_id) == 1
