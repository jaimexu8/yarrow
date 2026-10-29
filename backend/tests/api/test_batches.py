"""US-31: files uploaded together share a batch id, and the batch reports
accurate per-status counts without combining the jobs."""

import uuid

from sqlalchemy import select
from yarrow_db.models import Batch, Document, Job

from tests.conftest import DEFAULT_PASSWORD, FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


def _batch_url(batch_id) -> str:
    return f"{DOCUMENTS}batches/{batch_id}"


async def _upload(client, headers, batch_id=None, name="sample.pdf", upload_id=None):
    """One file per request, the way the Upload page sends them."""
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    data = {}
    if batch_id is not None:
        data["batch_id"] = str(batch_id)
    if upload_id is not None:
        data["client_upload_ids"] = upload_id
    return await client.post(
        UPLOAD,
        headers=headers,
        data=data,
        files=[("files", (name, pdf, "application/pdf"))],
    )


async def _upload_batch(client, headers, count: int) -> tuple[uuid.UUID, list[str]]:
    batch_id = uuid.uuid4()
    document_ids = []
    for index in range(count):
        response = await _upload(client, headers, batch_id, name=f"file-{index}.pdf")
        assert response.status_code == 200, response.text
        document_ids.append(response.json()["accepted"][0]["document_id"])
    return batch_id, document_ids


async def _set_status(db_session, document_id: str, status: str) -> None:
    """What the worker writes as it picks up, finishes or fails a job."""
    document = await db_session.get(Document, uuid.UUID(document_id))
    document.status = status
    job = (
        await db_session.execute(select(Job).where(Job.document_id == document.id))
    ).scalar_one()
    job.status = status
    await db_session.commit()


async def _other_user(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestSharedBatchId:
    """Criterion 1: one batch id for the run, but still one job per file."""

    async def test_files_of_one_run_share_the_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, document_ids = await _upload_batch(client, auth_headers, 3)

        batches = (await db_session.execute(select(Batch))).scalars().all()
        assert [batch.id for batch in batches] == [batch_id]
        for document_id in document_ids:
            document = await db_session.get(Document, uuid.UUID(document_id))
            assert document.batch_id == batch_id

    async def test_each_file_keeps_its_own_job(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        _batch_id, document_ids = await _upload_batch(client, auth_headers, 3)

        jobs = (await db_session.execute(select(Job))).scalars().all()
        assert sorted(str(job.document_id) for job in jobs) == sorted(document_ids)
        assert len(fake_queue) == 3

    async def test_upload_without_batch_id_belongs_to_no_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        response = await _upload(client, auth_headers)
        assert response.status_code == 200, response.text

        document_id = response.json()["accepted"][0]["document_id"]
        document = await db_session.get(Document, uuid.UUID(document_id))
        assert document.batch_id is None
        assert (await db_session.execute(select(Batch))).scalars().all() == []

    async def test_rejected_only_request_creates_no_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """A request whose files are all refused leaves no empty batch behind."""
        batch_id = uuid.uuid4()
        response = await client.post(
            UPLOAD,
            headers=auth_headers,
            data={"batch_id": str(batch_id)},
            files=[("files", ("notes.txt", b"plain text", "text/plain"))],
        )
        assert response.status_code == 400

        assert (await db_session.execute(select(Batch))).scalars().all() == []
        progress = await client.get(_batch_url(batch_id), headers=auth_headers)
        assert progress.status_code == 404

    async def test_storage_failure_creates_no_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session, monkeypatch
    ):
        """A file that could not be stored is refused as retryable, and since
        nothing was kept the run's batch is not created yet either."""

        def _storage_down(file, key):
            raise RuntimeError("bucket unreachable")

        monkeypatch.setattr(fake_storage, "upload_file", _storage_down)
        batch_id = uuid.uuid4()
        response = await _upload(client, auth_headers, batch_id=batch_id)
        assert response.status_code == 400
        assert response.json()["detail"][0]["retryable"] is True

        assert (await db_session.execute(select(Batch))).scalars().all() == []
        progress = await client.get(_batch_url(batch_id), headers=auth_headers)
        assert progress.status_code == 404

    async def test_retry_after_lost_reply_joins_the_run(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """The first attempt was stored but its reply never arrived, so the
        page retries with the same upload id. The stored document is counted
        in the run it is retried in rather than left outside every batch."""
        first = await _upload(client, auth_headers, upload_id="file-a")
        assert first.status_code == 200
        document_id = first.json()["accepted"][0]["document_id"]

        batch_id = uuid.uuid4()
        again = await _upload(client, auth_headers, batch_id, upload_id="file-a")
        assert again.status_code == 200
        assert again.json()["accepted"][0]["document_id"] == document_id

        document = await db_session.get(Document, uuid.UUID(document_id))
        assert document.batch_id == batch_id
        progress = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert progress["total"] == 1
        assert len(fake_storage.objects) == 1

    async def test_batch_id_must_be_a_uuid(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        response = await _upload(client, auth_headers, batch_id="not-a-uuid")
        assert response.status_code == 422

    async def test_cannot_upload_into_someone_elses_batch(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        batch_id, _ = await _upload_batch(client, auth_headers, 1)
        other = await _other_user(register, verify, login)

        response = await _upload(client, other, batch_id)
        assert response.status_code == 404
        assert len(fake_storage.objects) == 1

    async def test_retried_upload_is_not_counted_twice(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        batch_id = uuid.uuid4()
        first = await _upload(client, auth_headers, batch_id, upload_id="file-a")
        again = await _upload(client, auth_headers, batch_id, upload_id="file-a")
        assert first.status_code == again.status_code == 200
        assert (
            first.json()["accepted"][0]["document_id"]
            == again.json()["accepted"][0]["document_id"]
        )

        progress = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert progress["total"] == 1


class TestProgress:
    """Criteria 2 and 3: accurate counts, and one failure leaves the rest."""

    async def test_fresh_batch_is_all_queued(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        batch_id, _ = await _upload_batch(client, auth_headers, 3)

        response = await client.get(_batch_url(batch_id), headers=auth_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["batch_id"] == str(batch_id)
        assert body["total"] == 3
        assert body["queued"] == 3
        assert body["processing"] == 0
        assert body["completed"] == 0
        assert body["failed"] == 0
        assert body["canceled"] == 0
        assert body["created_at"] is not None

    async def test_counts_follow_each_documents_status(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, ids = await _upload_batch(client, auth_headers, 5)
        await _set_status(db_session, ids[0], "processing")
        await _set_status(db_session, ids[1], "completed")
        await _set_status(db_session, ids[2], "completed")
        await _set_status(db_session, ids[3], "failed")
        await _set_status(db_session, ids[4], "canceled")

        body = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert body["total"] == 5
        assert body["queued"] == 0
        assert body["processing"] == 1
        assert body["completed"] == 2
        assert body["failed"] == 1
        assert body["canceled"] == 1

    async def test_one_failure_does_not_fail_the_rest(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, ids = await _upload_batch(client, auth_headers, 3)
        await _set_status(db_session, ids[1], "failed")

        body = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert body["failed"] == 1
        assert body["queued"] == 2
        for document_id in (ids[0], ids[2]):
            document = await db_session.get(Document, uuid.UUID(document_id))
            assert document.status == "queued"
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert sorted(doc["status"] for doc in library) == [
            "failed",
            "queued",
            "queued",
        ]

    async def test_only_this_batchs_documents_are_counted(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first, _ = await _upload_batch(client, auth_headers, 2)
        second, _ = await _upload_batch(client, auth_headers, 1)
        await _upload(client, auth_headers)  # no batch at all

        assert (await client.get(_batch_url(first), headers=auth_headers)).json()[
            "total"
        ] == 2
        assert (await client.get(_batch_url(second), headers=auth_headers)).json()[
            "total"
        ] == 1


class TestPersistence:
    """Criterion 4: the batch outlives the page, so a later request still
    gets the latest state (the page only needs to remember the id)."""

    async def test_progress_is_read_from_the_database_each_time(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, ids = await _upload_batch(client, auth_headers, 2)
        before = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert before["completed"] == 0

        await _set_status(db_session, ids[0], "completed")

        after = (await client.get(_batch_url(batch_id), headers=auth_headers)).json()
        assert after["completed"] == 1
        assert after["queued"] == 1


class TestAccess:
    async def test_requires_auth(self, client):
        assert (await client.get(_batch_url(uuid.uuid4()))).status_code == 401

    async def test_unknown_batch_is_404(self, client, auth_headers):
        response = await client.get(_batch_url(uuid.uuid4()), headers=auth_headers)
        assert response.status_code == 404

    async def test_someone_elses_batch_is_404(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        batch_id, _ = await _upload_batch(client, auth_headers, 1)
        other = await _other_user(register, verify, login)

        response = await client.get(_batch_url(batch_id), headers=other)
        assert response.status_code == 404
        assert response.json() == {"detail": "Batch not found"}


class TestAccountDeletion:
    async def test_deleting_the_account_removes_its_batches(
        self, client, auth_headers, fake_storage, fake_queue, db_session, monkeypatch
    ):
        """Batches reference the user, so they must go with the account."""
        from app.services import account

        monkeypatch.setattr(account, "get_storage", lambda: fake_storage)
        batch_id, _ = await _upload_batch(client, auth_headers, 2)

        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=auth_headers,
            json={"password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 204, response.text
        assert await db_session.get(Batch, batch_id) is None
