"""GET /documents/{id}/regions/{region_id}/image: a figure's cropped picture"""

import uuid

import pytest
from yarrow_db.models import Page, Region
from yarrow_db.models.region import RegionImage

from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
PNG = b"\x89PNG fake crop"


def _image_url(document_id, region_id) -> str:
    return f"/api/v1/documents/{document_id}/regions/{region_id}/image"


async def _upload(client, headers) -> uuid.UUID:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _add_region(db_session, document_id, image_key: str | None) -> uuid.UUID:
    """A region on page 1 of the document, with an image row when given a key"""
    page = Page(id=uuid.uuid4(), document_id=document_id, page_number=1)
    region = Region(
        id=uuid.uuid4(), page_id=page.id, reading_order=0, region_type="figure"
    )
    db_session.add_all([page, region])
    if image_key is not None:
        db_session.add(RegionImage(region_id=region.id, image_key=image_key))
    await db_session.flush()
    return region.id


@pytest.mark.usefixtures("fake_queue")
class TestRegionImage:
    async def test_streams_the_crop(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/figures/crop.png"
        fake_storage.objects[key] = PNG
        region_id = await _add_region(db_session, document_id, key)

        response = await client.get(
            _image_url(document_id, region_id), headers=auth_headers
        )

        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert "private" in response.headers["cache-control"]

    async def test_region_without_image_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_region(db_session, document_id, None)

        response = await client.get(
            _image_url(document_id, region_id), headers=auth_headers
        )

        assert response.status_code == 404

    async def test_region_of_another_document_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        """Reading one document must not reach another's regions through it"""
        document_id = await _upload(client, auth_headers)
        other_id = await _upload(client, auth_headers)
        key = f"documents/{other_id}/figures/crop.png"
        fake_storage.objects[key] = PNG
        region_id = await _add_region(db_session, other_id, key)

        response = await client.get(
            _image_url(document_id, region_id), headers=auth_headers
        )

        assert response.status_code == 404

    async def test_other_users_cannot_read_it(
        self, client, auth_headers, fake_storage, db_session, register, verify, login
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/figures/crop.png"
        fake_storage.objects[key] = PNG
        region_id = await _add_region(db_session, document_id, key)

        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other_headers = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.get(
            _image_url(document_id, region_id), headers=other_headers
        )

        assert response.status_code == 404

    async def test_missing_object_is_not_found(
        self, client, auth_headers, fake_storage, db_session
    ):
        document_id = await _upload(client, auth_headers)
        key = f"documents/{document_id}/figures/gone.png"
        region_id = await _add_region(db_session, document_id, key)

        response = await client.get(
            _image_url(document_id, region_id), headers=auth_headers
        )

        assert response.status_code == 404
