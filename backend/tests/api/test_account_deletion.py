import uuid

import pytest
from sqlalchemy import func, select
from yarrow_db.models import Document, DocumentShare, Job, Page, Region, RegionImage, RegionTable, RegionText, Table, TableCell, User, Warning

from tests.conftest import DEFAULT_PASSWORD, FIXTURES_DIR, FakeStorage


@pytest.fixture
def storage(fake_storage, monkeypatch) -> FakeStorage:
    """The fake storage, also used by account deletion to remove files."""
    from app.services import account

    monkeypatch.setattr(account, "get_storage", lambda: fake_storage)
    return fake_storage


async def _upload(client, headers) -> uuid.UUID:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        "/api/v1/documents/upload",
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


# Populate user db and storage data, delete the user, verify the data are deleted
async def test_deleting_the_account_removes_its_data_and_files(client, db_session, storage, fake_queue, auth_headers, register):
    user_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"])
    other, _ = await register()
    other_id = (await db_session.scalars(select(User.id).where(User.email == other["email"]))).one()
    document_ids = [
        await _upload(client, auth_headers),
        await _upload(client, auth_headers),
    ]
    stored = {(await db_session.get(Document, document_id)).storage_key for document_id in document_ids}

    page = Page(id=uuid.uuid4(), document_id=document_ids[0], page_number=1)
    text, image, table_region = (Region(id=uuid.uuid4(), page_id=page.id, reading_order=order) for order in range(3))
    table = Table(id=uuid.uuid4(), document_id=document_ids[0], row_count=1, col_count=1)
    region_table = RegionTable(
        id=uuid.uuid4(),
        region_id=table_region.id,
        table_id=table.id,
        row_start=0,
        row_end=0,
        col_start=0,
        col_end=0,
    )
    image_key = f"images/{document_ids[0]}/figure-1.png"
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
            TableCell(region_table_id=region_table.id, row_idx=0, col_idx=0, text_content="42"),
            Warning(page_id=page.id, warning_type="blur", message="Page is blurry"),
            DocumentShare(
                document_id=document_ids[0],
                shared_with_user_id=other_id,
                permission="view",
            ),
        ]
    )
    await db_session.flush()
    storage.objects[image_key] = b"png"
    stored.add(image_key)
    assert stored <= storage.objects.keys()

    response = await client.request("DELETE", "/api/v1/auth/me", headers=auth_headers, json={"password": DEFAULT_PASSWORD})

    assert response.status_code == 204, response.text
    assert await db_session.scalar(select(User.id).where(User.id == user_id)) is None
    
    # Verifies that all documents, jobs, pages, tables, and shares related to the deleted user are removed
    for model, where in [
        (Document, Document.id.in_(document_ids)),
        (Job, Job.document_id.in_(document_ids)),
        (Page, Page.document_id.in_(document_ids)),
        (Table, Table.document_id.in_(document_ids)),
        (DocumentShare, DocumentShare.document_id.in_(document_ids)),
    ]:
        count = await db_session.scalar(select(func.count()).select_from(model).where(where))
        assert count == 0, model.__name__
    
    # Verifies rows reachable only through the deleted pages and regions are removed
    for model in (Region, RegionText, RegionImage, RegionTable, TableCell, Warning):
        count = await db_session.scalar(select(func.count()).select_from(model))
        assert count == 0, model.__name__
    
    assert not stored & storage.objects.keys()
    
    # Verifies that the other user is untouched.
    assert await db_session.scalar(select(User.id).where(User.id == other_id))


# Attempt to delete a user account with incorrect password and verify the deletion fails
async def test_wrong_password_does_not_delete_the_account(client, db_session, storage, fake_queue, auth_headers, login):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user_id, email = uuid.UUID(me["id"]), me["email"]
    document_id = await _upload(client, auth_headers)

    response = await client.request("DELETE", "/api/v1/auth/me", headers=auth_headers, json={"password": "not-my-password"})

    # Verifies that account deletion failed with an incorrect password
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert await db_session.scalar(select(User.id).where(User.id == user_id))
    assert await db_session.scalar(select(Document.id).where(Document.id == document_id))
    assert (await login(email, DEFAULT_PASSWORD)).status_code == 200


# Attempt to delete a user account with correct password and verify the deletion succeeds
async def test_correct_password_deletes_the_account(client, db_session, storage, auth_headers, login):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user_id, email = uuid.UUID(me["id"]), me["email"]
    response = await client.request("DELETE", "/api/v1/auth/me", headers=auth_headers, json={"password": DEFAULT_PASSWORD})

    # Verifies that the account deletion request succeeded
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert await db_session.scalar(select(User.id).where(User.id == user_id)) is None
    
    # Verifies that the account can no longer be used, by its old session or by signing in.
    assert (await client.get("/api/v1/auth/me", headers=auth_headers)).status_code == 401
    assert (await login(email, DEFAULT_PASSWORD)).status_code == 401


# Attempt to delete a user account when logged out and verify the deletion fails
@pytest.mark.parametrize("session", ["never_signed_in", "signed_out"])
async def test_logged_out_user_cannot_delete_the_account(client, db_session, storage, auth_headers, session):
    user_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"])
    headers = {}
    if session == "signed_out":
        logout = await client.post("/api/v1/auth/logout", headers=auth_headers)
        assert logout.status_code == 204, logout.text
        headers = auth_headers

    response = await client.request("DELETE", "/api/v1/auth/me", headers=headers, json={"password": DEFAULT_PASSWORD})

    # Verifies that a logged-out user cannot delete the account
    assert response.status_code == 401, response.text
    
    # Verifies that the user still exists in the database
    assert await db_session.scalar(select(User.id).where(User.id == user_id))
