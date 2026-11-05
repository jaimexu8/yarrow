"""GET /documents/{id}/pages/{page_number}/thumbnail: page previews for the viewer sidebar (US-98)"""

import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, DocumentShare, Job, Page, User

from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
DOCUMENTS = "/api/v1/documents/"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"
NOT_AVAILABLE = {
    "detail": {"detail": "This page has no thumbnail", "code": "THUMBNAIL_NOT_AVAILABLE"}
}


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from app.api.v1.endpoints import documents

    calls: list[str] = []
    monkeypatch.setattr(documents, "revoke_document_processing", calls.append)
    return calls


def _url(document_id, page_number: int) -> str:
    return f"/api/v1/documents/{document_id}/pages/{page_number}/thumbnail"


def _key(document_id, page_number: int) -> str:
    return f"documents/{document_id}/thumbnails/{page_number}.webp"


async def _upload(client, headers) -> tuple[uuid.UUID, uuid.UUID]:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        data={"allow_duplicate": "true"},
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    accepted = response.json()["accepted"][0]
    return uuid.UUID(accepted["document_id"]), uuid.UUID(accepted["job_id"])


async def _finish(db_session, job_id, page_count: int) -> None:
    job = await db_session.get(Job, job_id)
    document = await db_session.get(Document, job.document_id)
    job.status = document.status = "completed"
    job.current_stage = "finished"
    document.page_count = page_count
    await db_session.flush()


async def _add_page(db_session, document_id, page_number, thumbnail_key) -> None:
    db_session.add(
        Page(
            id=uuid.uuid4(),
            document_id=document_id,
            page_number=page_number,
            status="completed",
            thumbnail_key=thumbnail_key,
        )
    )
    await db_session.flush()


async def _other_user(register, verify, login, db_session) -> tuple[uuid.UUID, dict]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()["access_token"]
    user_id = (
        await db_session.execute(select(User.id).where(User.email == payload["email"]))
    ).scalar_one()
    return user_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("fake_queue")
class TestThumbnail:
    async def test_streams_the_stored_thumbnail(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        fake_storage.objects[_key(document_id, 1)] = WEBP
        await _add_page(db_session, document_id, 1, _key(document_id, 1))

        response = await client.get(_url(document_id, 1), headers=auth_headers)

        assert response.status_code == 200
        assert response.content == WEBP
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, max-age=3600"

    async def test_page_without_thumbnail_is_not_available(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        fake_storage.objects[_key(document_id, 1)] = WEBP
        await _add_page(db_session, document_id, 1, None)

        response = await client.get(_url(document_id, 1), headers=auth_headers)

        assert response.status_code == 404
        assert response.json() == NOT_AVAILABLE

    @pytest.mark.parametrize("page_number", [0, 2, 99])
    async def test_page_out_of_range_is_not_available(
        self, client, auth_headers, fake_storage, db_session, page_number
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        fake_storage.objects[_key(document_id, page_number)] = WEBP
        await _add_page(db_session, document_id, 1, _key(document_id, 1))

        response = await client.get(_url(document_id, page_number), headers=auth_headers)

        assert response.status_code == 404
        assert response.json() == NOT_AVAILABLE

    async def test_missing_object_is_not_available(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        await _add_page(db_session, document_id, 1, _key(document_id, 1))

        response = await client.get(_url(document_id, 1), headers=auth_headers)

        assert response.status_code == 404
        assert response.json() == NOT_AVAILABLE

    async def test_active_job_serves_the_deterministic_key(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        job = await db_session.get(Job, job_id)
        job.status = "processing"
        await db_session.flush()
        fake_storage.objects[_key(document_id, 2)] = WEBP

        served = await client.get(_url(document_id, 2), headers=auth_headers)
        not_yet = await client.get(_url(document_id, 3), headers=auth_headers)

        assert served.status_code == 200
        assert served.content == WEBP
        assert not_yet.status_code == 404
        assert not_yet.json() == NOT_AVAILABLE

    async def test_finished_job_does_not_serve_the_deterministic_key(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 0)
        fake_storage.objects[_key(document_id, 1)] = WEBP

        response = await client.get(_url(document_id, 1), headers=auth_headers)

        assert response.status_code == 404

    async def test_shared_reader_can_read(
        self, client, auth_headers, fake_storage, db_session, register, verify, login
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        fake_storage.objects[_key(document_id, 1)] = WEBP
        await _add_page(db_session, document_id, 1, _key(document_id, 1))
        reader_id, reader_headers = await _other_user(register, verify, login, db_session)
        db_session.add(
            DocumentShare(
                document_id=document_id, shared_with_user_id=reader_id, permission="view"
            )
        )
        await db_session.flush()

        response = await client.get(_url(document_id, 1), headers=reader_headers)

        assert response.status_code == 200
        assert response.content == WEBP

    async def test_stranger_gets_404(
        self, client, auth_headers, fake_storage, db_session, register, verify, login
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 1)
        fake_storage.objects[_key(document_id, 1)] = WEBP
        await _add_page(db_session, document_id, 1, _key(document_id, 1))
        _, stranger_headers = await _other_user(register, verify, login, db_session)

        response = await client.get(_url(document_id, 1), headers=stranger_headers)

        assert response.status_code == 404
        assert response.json() == {"detail": "Document not found"}

    async def test_responses_expose_has_thumbnail_not_keys(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 2)
        await _add_page(db_session, document_id, 1, _key(document_id, 1))
        await _add_page(db_session, document_id, 2, None)

        detail = (await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)).json()
        parsed = (
            await client.get(f"{DOCUMENTS}{document_id}/parsed", headers=auth_headers)
        ).json()

        for pages in (detail["pages"], parsed["pages"]):
            assert [page["has_thumbnail"] for page in pages] == [True, False]
            assert all("thumbnail_key" not in page for page in pages)


@pytest.mark.usefixtures("fake_queue", "revoked")
class TestDeletion:
    async def test_permanent_delete_removes_thumbnails_and_page_images(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id, job_id = await _upload(client, auth_headers)
        await _finish(db_session, job_id, 2)
        thumbnails = {_key(document_id, 1), _key(document_id, 2)}
        page_image = f"documents/{document_id}/pages/2.png"
        for key in thumbnails | {page_image}:
            fake_storage.objects[key] = WEBP
        await _add_page(db_session, document_id, 1, _key(document_id, 1))
        db_session.add(
            Page(
                id=uuid.uuid4(),
                document_id=document_id,
                page_number=2,
                status="completed",
                page_image_key=page_image,
                thumbnail_key=_key(document_id, 2),
            )
        )
        await db_session.flush()

        await client.delete(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        assert thumbnails <= fake_storage.objects.keys()
        response = await client.delete(
            f"{DOCUMENTS}{document_id}/permanent", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert not (thumbnails | {page_image}) & fake_storage.objects.keys()
