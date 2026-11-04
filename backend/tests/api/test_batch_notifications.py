"""US-60: a batch that has finished is detected on the server and announced to
its owner exactly once, with a summary of how its files ended up.

Announcing is two-phase: a claim leases the batch to one tab, and the tab's
acknowledgement makes it final. Only sealed runs (every file sent) are
announced."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from yarrow_db.config import db_settings
from yarrow_db.models import Batch, Document, Job

from app.core.config import settings
from app.services.batch_notifications import (
    claim_batch_notifications,
    is_finished,
)
from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
CLAIM = "/api/v1/documents/batches/notifications/claim"
ACK = "/api/v1/documents/batches/notifications/ack"
NOTHING = {"notifications": [], "pending": 0}


def _progress_url(batch_id) -> str:
    return f"/api/v1/documents/batches/{batch_id}"


def _seal_url(batch_id) -> str:
    return f"/api/v1/documents/batches/{batch_id}/seal"


def _ago(seconds: int) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds)


async def _upload(client, headers, batch_id, name="file.pdf", upload_id=None):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    # The same fixture every time; these tests are not about duplicates (US-79).
    data = {"allow_duplicate": "true"}
    if batch_id:
        data["batch_id"] = str(batch_id)
    if upload_id:
        data["client_upload_ids"] = upload_id
    response = await client.post(
        UPLOAD,
        headers=headers,
        data=data,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["accepted"][0]["document_id"]


async def _upload_batch(client, headers, count: int) -> tuple[uuid.UUID, list[str]]:
    """One file per request with a shared batch id, like the Upload page."""
    batch_id = uuid.uuid4()
    document_ids = [
        await _upload(client, headers, batch_id, name=f"file-{index}.pdf")
        for index in range(count)
    ]
    return batch_id, document_ids


async def _seal(client, headers, batch_id):
    response = await client.post(_seal_url(batch_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _set_statuses(db_session, document_ids, statuses) -> None:
    """What the worker (or cancel / reprocess) writes for each document."""
    for document_id, status in zip(document_ids, statuses, strict=True):
        document = await db_session.get(Document, uuid.UUID(document_id))
        document.status = status
        job = (
            await db_session.execute(select(Job).where(Job.document_id == document.id))
        ).scalar_one()
        job.status = status
    await db_session.commit()


async def _finished_batch(client, headers, db_session, statuses):
    """A run whose files were all sent (sealed) and have all ended."""
    batch_id, document_ids = await _upload_batch(client, headers, len(statuses))
    await _seal(client, headers, batch_id)
    await _set_statuses(db_session, document_ids, statuses)
    return batch_id, document_ids


async def _claim(client, headers):
    response = await client.post(CLAIM, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _ack(client, headers, batch_ids):
    response = await client.post(
        ACK, headers=headers, json={"batch_ids": [str(b) for b in batch_ids]}
    )
    assert response.status_code == 200, response.text
    return response.json()["acknowledged"]


async def _expire_lease(db_session, batch_id) -> None:
    """Pretend the claim was taken longer ago than the lease lasts."""
    await db_session.execute(
        update(Batch)
        .where(Batch.id == batch_id)
        .values(claimed_at=_ago(settings.BATCH_NOTIFICATION_LEASE_SECONDS + 5))
    )
    await db_session.commit()


async def _batch(db_session, batch_id) -> Batch:
    return await db_session.get(Batch, batch_id, populate_existing=True)


async def _other_user(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestStillRunning:
    """Nothing is announced while any file is still queued or processing, or
    while the run may still be sending files; ``pending`` tells the browser
    to keep checking (criterion 1)."""

    async def test_running_batch_is_not_announced(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, document_ids = await _upload_batch(client, auth_headers, 3)
        await _seal(client, auth_headers, batch_id)
        await _set_statuses(
            db_session, document_ids, ["completed", "failed", "processing"]
        )

        assert await _claim(client, auth_headers) == {
            "notifications": [],
            "pending": 1,
        }
        batch = await _batch(db_session, batch_id)
        assert batch.claimed_at is None
        assert batch.completed_at is None

    async def test_unsealed_batch_is_not_claimed_by_any_tab(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        # Tab A has sent one file and is still sending more. Tab B checks:
        # it knows nothing about A's run, and must not announce it.
        batch_id, document_ids = await _upload_batch(client, auth_headers, 1)
        await _set_statuses(db_session, document_ids, ["completed"])

        tab_b = await _claim(client, auth_headers)
        assert tab_b == {"notifications": [], "pending": 1}

        # Tab A sends its last file, then seals the run.
        last = await _upload(client, auth_headers, batch_id, name="last.pdf")
        await _set_statuses(db_session, [last], ["failed"])
        assert (await _claim(client, auth_headers))["notifications"] == []
        await _seal(client, auth_headers, batch_id)

        [notification] = (await _claim(client, auth_headers))["notifications"]
        assert notification["total"] == 2
        assert notification["completed"] == 1
        assert notification["failed"] == 1

    async def test_abandoned_run_is_sealed_by_the_timeout(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        # The tab closed mid-upload, so the run was never sealed.
        batch_id, document_ids = await _upload_batch(client, auth_headers, 2)
        await _set_statuses(db_session, document_ids, ["completed", "completed"])
        assert (await _claim(client, auth_headers))["notifications"] == []

        await db_session.execute(
            update(Document)
            .where(Document.batch_id == batch_id)
            .values(created_at=_ago(settings.BATCH_SEAL_TIMEOUT_SECONDS + 5))
        )
        await db_session.commit()

        progress = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert progress.json()["completed_at"] is not None
        [notification] = (await _claim(client, auth_headers))["notifications"]
        assert notification["batch_id"] == str(batch_id)

    async def test_no_batches_means_nothing_pending(self, client, auth_headers):
        assert await _claim(client, auth_headers) == NOTHING


class TestSeal:
    async def test_seal_is_idempotent(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _upload_batch(client, auth_headers, 1)

        first = await _seal(client, auth_headers, batch_id)
        second = await _seal(client, auth_headers, batch_id)

        assert first["batch_id"] == str(batch_id)
        assert first["sealed_at"] is not None
        assert second == first
        assert set(first) == {"batch_id", "sealed_at"}

    async def test_unknown_batch_is_404(self, client, auth_headers):
        response = await client.post(_seal_url(uuid.uuid4()), headers=auth_headers)
        assert response.status_code == 404

    async def test_someone_elses_batch_is_404(
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
        other = await _other_user(register, verify, login)
        theirs, _ = await _upload_batch(client, other, 1)

        response = await client.post(_seal_url(theirs), headers=auth_headers)

        assert response.status_code == 404
        assert (await _batch(db_session, theirs)).sealed_at is None

    async def test_requires_auth(self, client):
        response = await client.post(_seal_url(uuid.uuid4()))
        assert response.status_code == 401


class TestJoiningResets:
    """A file joining a batch reopens it: not finished, not sealed, and any
    unacknowledged claim (with its now-stale counts) is dropped."""

    async def test_new_file_clears_completed_and_sealed(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        progress = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert progress.json()["completed_at"] is not None

        # Upload anyway / another file into the same run.
        await _upload(client, auth_headers, batch_id, name="more.pdf")

        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is None
        assert batch.sealed_at is None

    async def test_retry_joining_the_run_clears_completed(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        await client.get(_progress_url(batch_id), headers=auth_headers)
        assert (await _batch(db_session, batch_id)).completed_at is not None
        # A file first stored without the batch (its reply was lost) ...
        earlier = await _upload(
            client, auth_headers, None, name="lost.pdf", upload_id="retry-1"
        )
        await _set_statuses(db_session, [earlier], ["completed"])

        # ... is retried with the run's id, and joins it.
        await _upload(
            client, auth_headers, batch_id, name="lost.pdf", upload_id="retry-1"
        )

        document = await db_session.get(
            Document, uuid.UUID(earlier), populate_existing=True
        )
        assert document.batch_id == batch_id
        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is None
        assert batch.sealed_at is None

    async def test_joining_drops_an_unacknowledged_claim(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        assert len((await _claim(client, auth_headers))["notifications"]) == 1

        await _upload(client, auth_headers, batch_id, name="more.pdf")

        # The ack for the old, partial summary no longer counts ...
        assert await _ack(client, auth_headers, [batch_id]) == []
        # ... and once the run is sealed and done, the full one is offered.
        await _seal(client, auth_headers, batch_id)
        documents = (
            (
                await db_session.execute(
                    select(Document.id).where(Document.batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
        await _set_statuses(
            db_session, [str(d) for d in documents], ["completed"] * len(documents)
        )
        [notification] = (await _claim(client, auth_headers))["notifications"]
        assert notification["total"] == 2


class TestCompletionSummary:
    """Criterion 2: a finished batch is announced with its outcomes."""

    async def test_all_successful_batch(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"] * 3
        )

        body = await _claim(client, auth_headers)

        [notification] = body["notifications"]
        assert notification["batch_id"] == str(batch_id)
        assert notification["total"] == 3
        assert notification["completed"] == 3
        assert notification["failed"] == 0
        assert notification["canceled"] == 0
        assert notification["completed_at"] is not None
        assert set(notification) == {
            "batch_id",
            "total",
            "completed",
            "failed",
            "canceled",
            "created_at",
            "completed_at",
        }
        # Leased but not acknowledged yet: still pending.
        assert body["pending"] == 1
        await _ack(client, auth_headers, [batch_id])
        assert await _claim(client, auth_headers) == NOTHING

    async def test_mixed_outcomes_are_summarized(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _finished_batch(
            client,
            auth_headers,
            db_session,
            ["completed", "completed", "failed", "canceled", "failed"],
        )

        [notification] = (await _claim(client, auth_headers))["notifications"]

        assert notification["total"] == 5
        assert notification["completed"] == 2
        assert notification["failed"] == 2
        assert notification["canceled"] == 1

    async def test_trashed_documents_are_left_out(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, document_ids = await _upload_batch(client, auth_headers, 3)
        await _seal(client, auth_headers, batch_id)
        await _set_statuses(db_session, document_ids, ["completed", "failed", "queued"])
        # The queued one is in trash (deleted_at set; status as stored).
        trashed = await db_session.get(Document, uuid.UUID(document_ids[2]))
        trashed.deleted_at = _ago(1)
        await db_session.commit()

        progress = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert progress.json()["total"] == 2
        assert progress.json()["queued"] == 0
        [notification] = (await _claim(client, auth_headers))["notifications"]
        assert notification["total"] == 2
        assert notification["completed"] == 1
        assert notification["failed"] == 1

    async def test_batch_with_only_trashed_documents_is_never_announced(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        _batch_id, document_ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        trashed = await db_session.get(Document, uuid.UUID(document_ids[0]))
        trashed.deleted_at = _ago(1)
        await db_session.commit()

        assert await _claim(client, auth_headers) == NOTHING

    async def test_completion_is_persisted(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed", "failed"]
        )

        await _claim(client, auth_headers)
        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is not None
        assert batch.claimed_at is not None
        assert batch.notified_at is None

        await _ack(client, auth_headers, [batch_id])
        assert (await _batch(db_session, batch_id)).notified_at is not None

    async def test_several_finished_batches_are_each_announced(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        first, _ = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        second, _ = await _finished_batch(client, auth_headers, db_session, ["failed"])

        body = await _claim(client, auth_headers)

        assert {n["batch_id"] for n in body["notifications"]} == {
            str(first),
            str(second),
        }

    async def test_progress_reports_when_the_batch_finished(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, document_ids = await _upload_batch(client, auth_headers, 2)
        await _set_statuses(db_session, document_ids, ["completed", "failed"])

        unsealed = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert unsealed.json()["completed_at"] is None

        await _seal(client, auth_headers, batch_id)
        finished = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert finished.json()["completed_at"] is not None
        batch = await _batch(db_session, batch_id)
        # Detecting it is not announcing it: the claim still has it.
        assert batch.claimed_at is None
        assert len((await _claim(client, auth_headers))["notifications"]) == 1


class TestDelivery:
    """Criterion 4, and never losing a notification on the way."""

    async def test_claimed_batch_is_not_offered_to_another_tab(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _finished_batch(client, auth_headers, db_session, ["completed"] * 2)

        first = await _claim(client, auth_headers)
        second = await _claim(client, auth_headers)

        assert len(first["notifications"]) == 1
        assert second == {"notifications": [], "pending": 1}

    async def test_lease_expiry_redelivers_an_unacknowledged_claim(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        # The reply was lost, or the tab closed before showing it.
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed", "failed"]
        )
        assert len((await _claim(client, auth_headers))["notifications"]) == 1

        await _expire_lease(db_session, batch_id)

        [again] = (await _claim(client, auth_headers))["notifications"]
        assert again["batch_id"] == str(batch_id)
        assert again["failed"] == 1

    async def test_ack_prevents_redelivery(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        await _claim(client, auth_headers)

        assert await _ack(client, auth_headers, [batch_id]) == [str(batch_id)]
        # A repeated ack changes nothing.
        assert await _ack(client, auth_headers, [batch_id]) == []

        await _expire_lease(db_session, batch_id)
        assert await _claim(client, auth_headers) == NOTHING

    async def test_ack_of_an_unclaimed_batch_does_nothing(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )

        assert await _ack(client, auth_headers, [batch_id]) == []
        assert len((await _claim(client, auth_headers))["notifications"]) == 1

    async def test_another_session_of_the_same_user_is_not_notified_again(
        self, client, fake_storage, fake_queue, db_session, register, verify, login
    ):
        # Two tabs or devices: two separate sign-ins of the same account.
        payload, _ = await register()
        await verify(payload["email"])
        tabs = []
        for _ in range(2):
            token = (await login(payload["email"], payload["password"])).json()
            tabs.append({"Authorization": f"Bearer {token['access_token']}"})
        batch_id, _ids = await _finished_batch(
            client, tabs[0], db_session, ["completed"]
        )

        assert len((await _claim(client, tabs[0]))["notifications"]) == 1
        await _ack(client, tabs[0], [batch_id])
        await _expire_lease(db_session, batch_id)
        assert await _claim(client, tabs[1]) == NOTHING

    async def test_reading_progress_again_does_not_renotify(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        await _claim(client, auth_headers)
        await _ack(client, auth_headers, [batch_id])

        progress = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert progress.status_code == 200

        assert (await _claim(client, auth_headers))["notifications"] == []

    async def test_reprocessed_document_does_not_renotify(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, document_ids = await _finished_batch(
            client, auth_headers, db_session, ["completed", "failed"]
        )
        await _claim(client, auth_headers)
        await _ack(client, auth_headers, [batch_id])

        # The failed file is sent back to the queue, then finishes again.
        await _set_statuses(db_session, document_ids, ["completed", "queued"])
        running = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert running.json()["completed_at"] is None
        assert await _claim(client, auth_headers) == NOTHING

        await _set_statuses(db_session, document_ids, ["completed", "completed"])
        finished = await client.get(_progress_url(batch_id), headers=auth_headers)
        assert finished.json()["completed_at"] is not None
        assert (await _claim(client, auth_headers))["notifications"] == []


class TestConcurrentClaims:
    """Many tabs claiming at the same moment get each batch exactly once.

    This needs real, committed rows seen by separate connections, so unlike
    the other tests it does not run inside the rolled-back test transaction;
    it removes what it created afterwards.
    """

    async def test_concurrent_claims_get_the_batch_exactly_once(self):
        engine = create_async_engine(db_settings.async_url, poolclass=NullPool)
        owner_id, batch_id = uuid.uuid4(), uuid.uuid4()
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, email, hashed_password) "
                        "VALUES (:id, :email, 'x')"
                    ),
                    {"id": owner_id, "email": f"race-{owner_id.hex}@example.com"},
                )
                await conn.execute(
                    text(
                        "INSERT INTO batches (id, owner_id, created_at, sealed_at) "
                        "VALUES (:id, :owner, now(), now())"
                    ),
                    {"id": batch_id, "owner": owner_id},
                )
                for index, state in enumerate(["completed", "failed", "canceled"]):
                    await conn.execute(
                        text(
                            "INSERT INTO documents (id, owner_id, filename, "
                            "file_size_bytes, file_type, storage_key, status, "
                            "batch_id, created_at) VALUES (:id, :owner, :name, 1, "
                            "'application/pdf', :key, :status, :batch, now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "owner": owner_id,
                            "name": f"f{index}.pdf",
                            "key": f"race/{batch_id}/{index}",
                            "status": state,
                            "batch": batch_id,
                        },
                    )

            async def one_tab():
                async with AsyncSession(engine) as session:
                    return await claim_batch_notifications(session, owner_id)

            results = await asyncio.gather(*[one_tab() for _ in range(10)])

            claimed = [n for result in results for n in result.notifications]
            assert [n.batch_id for n in claimed] == [batch_id]
            assert (claimed[0].completed, claimed[0].failed) == (1, 1)
            assert claimed[0].canceled == 1
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM documents WHERE owner_id = :o"), {"o": owner_id}
                )
                await conn.execute(
                    text("DELETE FROM batches WHERE owner_id = :o"), {"o": owner_id}
                )
                await conn.execute(
                    text("DELETE FROM users WHERE id = :o"), {"o": owner_id}
                )
            await engine.dispose()


class TestAccess:
    async def test_claim_requires_auth(self, client):
        response = await client.post(CLAIM)
        assert response.status_code == 401

    async def test_ack_requires_auth(self, client):
        response = await client.post(ACK, json={"batch_ids": [str(uuid.uuid4())]})
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "body",
        [{}, {"batch_ids": []}, {"batch_ids": ["nope"]}, {"ids": [], "batch_ids": []}],
    )
    async def test_ack_validates_its_body(self, client, auth_headers, body):
        response = await client.post(ACK, headers=auth_headers, json=body)
        assert response.status_code == 422

    async def test_someone_elses_batch_is_never_announced_to_me(
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
        other = await _other_user(register, verify, login)
        theirs, _ = await _finished_batch(client, other, db_session, ["completed"])

        assert await _claim(client, auth_headers) == NOTHING
        progress = await client.get(_progress_url(theirs), headers=auth_headers)
        assert progress.status_code == 404

        # Still theirs to be told about, and my ack cannot suppress it.
        body = await _claim(client, other)
        assert [n["batch_id"] for n in body["notifications"]] == [str(theirs)]
        assert await _ack(client, auth_headers, [theirs]) == []
        assert (await _batch(db_session, theirs)).notified_at is None
        assert await _ack(client, other, [theirs]) == [str(theirs)]


class TestIsFinished:
    """The completion rule on its own."""

    def test_every_final_status_counts_as_finished(self):
        assert is_finished({"completed": 2, "failed": 1, "canceled": 1})

    def test_anything_queued_or_processing_is_not_finished(self):
        assert not is_finished({"completed": 2, "queued": 1})
        assert not is_finished({"completed": 2, "processing": 1})

    def test_unknown_status_is_not_finished(self):
        assert not is_finished({"completed": 1, "paused": 1})

    def test_empty_batch_is_not_finished(self):
        assert not is_finished({})
        assert not is_finished({"completed": 0, "failed": 0})


async def _resumable(client, headers, content: bytes, batch_id=None) -> str:
    """A resumable upload (US-80) with every chunk sent, not yet finalized."""
    body = {
        "filename": "big.pdf",
        "total_size_bytes": len(content),
        "chunk_size_bytes": 64 * 1024,
    }
    if batch_id is not None:
        body["batch_id"] = str(batch_id)
    created = await client.post("/api/v1/uploads", headers=headers, json=body)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    chunk = await client.put(
        f"/api/v1/uploads/{session_id}/chunks/0",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert chunk.status_code == 200, chunk.text
    return session_id


class TestResumableJoins:
    """US-80 x US-60: a large file finalized into a run joins it through the
    same _get_or_create_batch, so it reopens the run like any other file."""

    def _unique_pdf(self) -> bytes:
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        return pdf + f"\n% {uuid.uuid4()}\n".encode()

    async def test_finalize_into_a_finished_run_reopens_it(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        assert len((await _claim(client, auth_headers))["notifications"]) == 1
        session_id = await _resumable(
            client, auth_headers, self._unique_pdf(), batch_id
        )

        done = await client.post(
            f"/api/v1/uploads/{session_id}/finalize",
            headers=auth_headers,
            json={"batch_id": str(batch_id)},
        )
        assert done.status_code == 200, done.text
        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is None
        assert batch.sealed_at is None
        assert batch.claimed_at is None
        # The stale claim's ack is a no-op; the run is announced once it is
        # sealed again and done.
        assert await _ack(client, auth_headers, [batch_id]) == []

    async def test_repeat_finalize_joining_a_finished_run_reopens_it(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        session_id = await _resumable(client, auth_headers, self._unique_pdf())
        url = f"/api/v1/uploads/{session_id}/finalize"
        first = await client.post(url, headers=auth_headers)
        assert first.status_code == 200, first.text
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        await client.get(_progress_url(batch_id), headers=auth_headers)
        assert (await _batch(db_session, batch_id)).completed_at is not None

        # The lost reply is retried in the run: the unbatched document joins.
        again = await client.post(
            url, headers=auth_headers, json={"batch_id": str(batch_id)}
        )
        assert again.json()["document_id"] == first.json()["document_id"]
        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is None
        assert batch.sealed_at is None

    async def test_duplicate_refused_at_finalize_leaves_the_run_alone(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        batch_id, _ids = await _finished_batch(
            client, auth_headers, db_session, ["completed"]
        )
        await client.get(_progress_url(batch_id), headers=auth_headers)
        identical = (FIXTURES_DIR / "sample.pdf").read_bytes()
        session_id = await _resumable(client, auth_headers, identical, batch_id)

        refused = await client.post(
            f"/api/v1/uploads/{session_id}/finalize",
            headers=auth_headers,
            json={"batch_id": str(batch_id)},
        )
        assert refused.status_code == 409
        batch = await _batch(db_session, batch_id)
        assert batch.completed_at is not None
        assert batch.sealed_at is not None
