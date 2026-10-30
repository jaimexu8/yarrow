"""Test bulk delete and reprocess for several documents at once."""

import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Job

from app.services import document_deletion
from app.services.document_deletion import (
    DocumentDeletionError,
    DocumentDeletionOutcome,
)
from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"
BULK_DELETE = "/api/v1/documents/bulk-delete"
BULK_REPROCESS = "/api/v1/documents/bulk-reprocess"


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _two_documents(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    first = await _upload(client, headers)
    second = await _upload(client, headers)
    return first[0], second[0]


def _by_id(results: list[dict], document_id: uuid.UUID) -> dict:
    return next(item for item in results if item["document_id"] == str(document_id))


@pytest.fixture
def revoked(monkeypatch):
    """Records revoked task ids instead of talking to Celery."""
    from app.api.v1.endpoints import documents

    calls: list[str] = []
    monkeypatch.setattr(documents, "revoke_document_processing", calls.append)
    return calls


class TestBulkDelete:
    async def test_deletes_every_document_in_the_list(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        keep_id, _ = await _upload(client, auth_headers)
        first_id, second_id = await _two_documents(client, auth_headers)

        response = await client.post(
            BULK_DELETE,
            headers=auth_headers,
            json={"document_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert {item["ok"] for item in body} == {True}
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(keep_id)]
        # The deleted documents' queued tasks are revoked; the kept one is not.
        assert sorted(revoked) == ["task-2", "task-3"]

    async def test_storage_quota_is_refunded_for_each_document(
        self, client, auth_headers, fake_storage, fake_queue, revoked, db_session
    ):
        from yarrow_db.models import User

        first_id, second_id = await _two_documents(client, auth_headers)
        owner_id = (await db_session.get(Document, first_id)).owner_id
        owner = await db_session.get(User, owner_id)
        await db_session.refresh(owner)
        sizes = 0
        for doc_id in (first_id, second_id):
            sizes += (await db_session.get(Document, doc_id)).file_size_bytes
        used_before = owner.storage_used_bytes

        await client.post(
            BULK_DELETE,
            headers=auth_headers,
            json={"document_ids": [str(first_id), str(second_id)]},
        )

        await db_session.refresh(owner)
        assert owner.storage_used_bytes == used_before - sizes

    async def test_a_busy_document_is_reported_and_the_rest_are_deleted(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        revoked,
        db_session,
        monkeypatch,
    ):
        first_id, second_id = await _two_documents(client, auth_headers)

        from app.api.v1.endpoints import documents

        async def _busy(db, document_id):
            if document_id == second_id:
                return DocumentDeletionOutcome(
                    error=DocumentDeletionError.BUSY, detail="try again shortly"
                )
            return await document_deletion.delete_document(db, document_id)

        monkeypatch.setattr(documents, "delete_document", _busy)

        response = await client.post(
            BULK_DELETE,
            headers=auth_headers,
            json={"document_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert _by_id(body, first_id)["ok"] is True
        busy = _by_id(body, second_id)
        assert busy["ok"] is False
        # Only the non-busy document is gone; the busy one is left for a retry.
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(second_id)]

    async def test_another_users_document_is_not_deleted(
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
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.post(
            BULK_DELETE,
            headers=other,
            json={"document_ids": [str(document_id)]},
        )

        # Reported as not ok (indistinguishable from a missing document); nothing deleted.
        assert response.status_code == 200, response.text
        assert response.json() == [
            {
                "document_id": str(document_id),
                "ok": False,
                "message": "Document not found",
            }
        ]
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [doc["id"] for doc in library] == [str(document_id)]

    async def test_empty_list_is_a_validation_error(self, client, auth_headers):
        response = await client.post(
            BULK_DELETE, headers=auth_headers, json={"document_ids": []}
        )
        assert response.status_code == 422

    async def test_requires_sign_in(self, client):
        response = await client.post(
            BULK_DELETE, json={"document_ids": [str(uuid.uuid4())]}
        )
        assert response.status_code == 401


class TestBulkReprocess:
    async def test_reprocesses_every_document_in_the_list(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        from tests.api.test_reprocess import _finish

        first_id, second_id = await _two_documents(client, auth_headers)
        for doc_id in (first_id, second_id):
            job = (
                await db_session.scalars(
                    select(Job)
                    .where(Job.document_id == doc_id)
                    .order_by(Job.created_at.desc())
                    .limit(1)
                )
            ).first()
            await _finish(db_session, doc_id, job.id, {1: "completed", 2: "completed"})

        queued_before = len(fake_queue)
        response = await client.post(
            BULK_REPROCESS,
            headers=auth_headers,
            json={"document_ids": [str(first_id), str(second_id)]},
        )

        assert response.status_code == 200, response.text
        assert {item["ok"] for item in response.json()} == {True}
        # A fresh job was queued for each document.
        assert len(fake_queue) == queued_before + 2

    async def test_a_running_document_is_reported_and_the_rest_are_queued(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        from tests.api.test_reprocess import _finish, _set_status

        first_id, second_id = await _two_documents(client, auth_headers)
        first_job = (
            await db_session.scalars(
                select(Job)
                .where(Job.document_id == first_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).first()
        second_job = (
            await db_session.scalars(
                select(Job)
                .where(Job.document_id == second_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).first()
        # Both finish, then the first one starts a fresh (live) job.
        await _finish(db_session, first_id, first_job.id, {1: "completed"})
        await _finish(db_session, second_id, second_job.id, {1: "completed"})
        await _set_status(db_session, first_id, first_job.id, "processing")

        queued_before = len(fake_queue)
        # The completed document is processed first (its commit keeps the
        # other document's state); the running one is reported and skipped.
        response = await client.post(
            BULK_REPROCESS,
            headers=auth_headers,
            json={"document_ids": [str(second_id), str(first_id)]},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert _by_id(body, first_id)["ok"] is False
        assert _by_id(body, second_id)["ok"] is True
        # Only the reprocessable document got a new job.
        assert len(fake_queue) == queued_before + 1

    async def test_another_users_document_is_not_reprocessed(
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
        from tests.api.test_reprocess import _finish

        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, document_id, job_id, {1: "completed"})
        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.post(
            BULK_REPROCESS,
            headers=other,
            json={"document_ids": [str(document_id)]},
        )

        assert response.status_code == 200, response.text
        assert response.json() == [
            {
                "document_id": str(document_id),
                "ok": False,
                "message": "Document not found",
            }
        ]

    async def test_empty_list_is_a_validation_error(self, client, auth_headers):
        response = await client.post(
            BULK_REPROCESS, headers=auth_headers, json={"document_ids": []}
        )
        assert response.status_code == 422

    async def test_requires_sign_in(self, client):
        response = await client.post(
            BULK_REPROCESS, json={"document_ids": [str(uuid.uuid4())]}
        )
        assert response.status_code == 401
