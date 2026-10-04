"""Test deleting a document and everything extracted from it"""

import uuid

import pytest
from sqlalchemy import func, select
from yarrow_db.models import (
    Document,
    DocumentShare,
    Job,
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
    DocumentDeletionError,
    DocumentDeletionOutcome,
)
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


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
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


class TestDeleteOwnDocument:
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

    async def test_extracted_data_and_files_are_deleted(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        """Ensure that extracted data and associated files are deleted when a document is removed."""
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
        for model, where in [
            (Document, Document.id == document_id),
            (Job, Job.document_id == document_id),
            (Page, Page.document_id == document_id),
            (Table, Table.document_id == document_id),
            (DocumentShare, DocumentShare.document_id == document_id),
        ]:
            assert await _count(db_session, model, where) == 0, model.__name__
        # Rows reachable only through the page and region ids.
        for model in (Region, RegionText, RegionImage, RegionTable, TableCell, Warning):
            assert await _count(db_session, model) == 0, model.__name__
        assert not stored & fake_storage.objects.keys()

    async def test_storage_quota_is_refunded(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        """Ensure that deleting a document refunds the storage quota"""
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        owner = await db_session.get(User, document.owner_id)
        await db_session.refresh(owner)
        used_before = owner.storage_used_bytes
        assert used_before >= document.file_size_bytes > 0

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before - document.file_size_bytes

    async def test_quota_never_goes_negative(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        """Ensure that storage quota never goes negative"""
        document_id, _ = await _upload(client, auth_headers)
        document = await db_session.get(Document, document_id)
        owner = await db_session.get(User, document.owner_id)
        owner.storage_used_bytes = 1
        await db_session.flush()

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)

        await db_session.refresh(owner)
        assert owner.storage_used_bytes == 0

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

    async def test_storage_failure_does_not_fail_the_delete(
        self, client, auth_headers, fake_storage, fake_queue, revoked, monkeypatch
    ):
        """The rows are already committed, so the document is gone either way;
        the leftover file is logged for cleanup instead."""

        def _storage_down(key):
            raise ConnectionError("storage unavailable")

        document_id, _ = await _upload(client, auth_headers)
        monkeypatch.setattr(fake_storage, "delete_file", _storage_down)

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

        monkeypatch.setattr(documents, "delete_document", _busy)
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
