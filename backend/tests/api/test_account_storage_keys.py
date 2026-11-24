"""Account deletion has to remove every object its rows point at.

The document rows own the original file. Page rows own thumbnails and upright
page images, and region images own figure crops. Deleting the rows without
collecting all of those keys leaves objects in storage with no DB reference.
"""

import uuid

import pytest
from sqlalchemy import select
from yarrow_db.models import Document, Page, Region, RegionImage, User

from tests.conftest import DEFAULT_PASSWORD, FakeStorage


@pytest.fixture
def storage(fake_storage, monkeypatch) -> FakeStorage:
    """The fake storage, also used by account deletion to remove files."""
    from app.services import account

    monkeypatch.setattr(account, "get_storage", lambda: fake_storage)
    return fake_storage


async def test_deleting_account_removes_thumbnails_page_images_and_originals(
    client, db_session, storage, fake_queue, auth_headers
):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user_id = uuid.UUID(me["id"])

    document = Document(
        id=uuid.uuid4(),
        owner_id=user_id,
        filename="scan.pdf",
        file_size_bytes=10,
        file_type="application/pdf",
        storage_key=f"documents/{uuid.uuid4()}/original",
        status="completed",
        page_count=1,
    )
    page = Page(
        id=uuid.uuid4(),
        document_id=document.id,
        page_number=1,
        status="completed",
        thumbnail_key=f"documents/{document.id}/thumbnails/1.webp",
        page_image_key=f"documents/{document.id}/pages/1.png",
    )
    region = Region(id=uuid.uuid4(), page_id=page.id, reading_order=0)
    figure = RegionImage(
        region_id=region.id,
        image_key=f"images/{document.id}/figure-1.png",
    )
    db_session.add_all([document, page, region, figure])
    await db_session.flush()

    keys = [
        document.storage_key,
        page.thumbnail_key,
        page.page_image_key,
        figure.image_key,
    ]
    for key in keys:
        storage.objects[key] = b"stored"
    assert set(keys) <= storage.objects.keys()

    response = await client.request(
        "DELETE",
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"password": DEFAULT_PASSWORD},
    )

    assert response.status_code == 204, response.text
    assert await db_session.scalar(select(User.id).where(User.id == user_id)) is None
    assert not set(keys) & storage.objects.keys()
