"""GET /documents/{id}/pages/{page_number}/image: a rotated page's upright image"""

import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Page, Warning

from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
PARSED = "/api/v1/documents/{id}/parsed"
PNG = b"\x89PNG fake page"


def _image_url(document_id, page_number: int) -> str:
    return f"/api/v1/documents/{document_id}/pages/{page_number}/image"


async def _upload(client, headers) -> uuid.UUID:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _add_page(db_session, document_id, page_number, image_key: str | None) -> uuid.UUID:
    """A page of the document, with a stored upright image when given a key"""
    result = await db_session.execute(
        select(Page).where(
            Page.document_id == document_id, Page.page_number == page_number
        )
    )
    page = result.scalar_one_or_none()
    if page is None:
        page = Page(
            id=uuid.uuid4(), document_id=document_id, page_number=page_number
        )
        db_session.add(page)
    page.page_image_key = image_key
    await db_session.flush()
    return page.id


@pytest.mark.usefixtures("fake_queue")
class TestPageImage:
    async def test_streams_the_upright_page(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/pages/1.png"
        fake_storage.objects[key] = PNG
        await _add_page(db_session, document_id, 1, key)

        response = await client.get(
            _image_url(document_id, 1), headers=auth_headers
        )

        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert "private" in response.headers["cache-control"]

    async def test_page_without_image_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_page(db_session, document_id, 1, None)

        response = await client.get(
            _image_url(document_id, 1), headers=auth_headers
        )

        assert response.status_code == 404

    async def test_other_users_cannot_read_it(
        self, client, auth_headers, fake_storage, db_session, register, verify, login
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/pages/1.png"
        fake_storage.objects[key] = PNG
        await _add_page(db_session, document_id, 1, key)

        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other_headers = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.get(
            _image_url(document_id, 1), headers=other_headers
        )

        assert response.status_code == 404

    async def test_missing_object_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/pages/gone.png"
        await _add_page(db_session, document_id, 1, key)

        response = await client.get(
            _image_url(document_id, 1), headers=auth_headers
        )

        assert response.status_code == 404

    async def test_page_of_another_document_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        """Reading one document must not reach another's pages through it"""
        document_id = await _upload(client, auth_headers)
        other_id = await _upload(client, auth_headers)
        key = f"documents/{other_id}/pages/1.png"
        fake_storage.objects[key] = PNG
        await _add_page(db_session, other_id, 1, key)

        response = await client.get(
            _image_url(document_id, 1), headers=auth_headers
        )

        assert response.status_code == 404

    async def test_parsed_tree_surfaces_image_key_and_rotation_warning(
        self, client, auth_headers, fake_storage, db_session
    ):
        """The same key the endpoint streams is what the tree hands the viewer."""
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/pages/2.png"
        await _add_page(db_session, document_id, 2, key)
        page_id = await _add_page(db_session, document_id, 1, None)
        db_session.add(
            Warning(
                page_id=page_id,
                warning_type="quality",
                message="Page was auto-rotated by 90°",
            )
        )
        document = await db_session.get(Document, document_id)
        document.status = "completed"
        document.page_count = 2
        await db_session.flush()

        response = await client.get(PARSED.format(id=document_id), headers=auth_headers)

        assert response.status_code == 200
        pages = {page["page_number"]: page for page in response.json()["pages"]}
        assert pages[2]["image_key"] == key
        assert pages[1]["image_key"] is None
        assert [warning["message"] for warning in pages[1]["warnings"]] == [
            "Page was auto-rotated by 90°"
        ]
