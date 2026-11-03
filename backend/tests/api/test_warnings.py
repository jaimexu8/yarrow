"""Quality warnings listed across the caller's documents, each with its page."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from yarrow_db.models import Document, DocumentShare, Page, Warning

from tests.conftest import FIXTURES_DIR

WARNINGS_PATH = "/api/v1/warnings/"
UPLOAD_PATH = "/api/v1/documents/upload"


async def upload_document(client, headers, name="sample.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD_PATH,
        headers=headers,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def add_warning(db_session, document_id, page_number, message):
    result = await db_session.execute(
        select(Page).where(
            Page.document_id == document_id, Page.page_number == page_number
        )
    )
    
    page = result.scalar_one_or_none()
    
    if page is None:
        page = Page(id=uuid.uuid4(), document_id=document_id, page_number=page_number)
        db_session.add(page)
        await db_session.flush()

    db_session.add(Warning(page_id=page.id, warning_type="quality", message=message))
    await db_session.flush()


async def _other_headers(register, verify, login):
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestWarnings:
    async def test_requires_auth(self, client):
        """Test that the warnings endpoint requires authentication"""
        assert (await client.get(WARNINGS_PATH)).status_code == 401

    async def test_empty_without_warnings(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Test that the warnings endpoint returns an empty list when there are no warnings"""
        
        await upload_document(client, auth_headers)

        response = await client.get(WARNINGS_PATH, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_one_entry_per_warning_with_its_page(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """Test that each warning generates one entry and is associated with its page"""
        
        document_id = await upload_document(client, auth_headers, "scan.pdf")
        await add_warning(db_session, document_id, 3, "Image is truncated")
        await add_warning(db_session, document_id, 1, "Image is blurry")
        await add_warning(db_session, document_id, 3, "Image is skewed by 6.2°")

        response = await client.get(WARNINGS_PATH, headers=auth_headers)
        assert response.status_code == 200
        body = response.json()

        # Page order within the document; a page with two warnings has two entries
        assert [(entry["page_number"], entry["message"]) for entry in body] == [
            (1, "Image is blurry"),
            (3, "Image is truncated"),
            (3, "Image is skewed by 6.2°"),
        ]
        assert all(entry["document_id"] == str(document_id) for entry in body)
        assert all(entry["filename"] == "scan.pdf" for entry in body)
        assert all(entry["warning_type"] == "quality" for entry in body)

    async def test_newest_document_first(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """Test that warnings are listed with the newest document first"""
        
        older = await upload_document(client, auth_headers, "older.pdf")
        newer = await upload_document(client, auth_headers, "newer.pdf")
        now = datetime.now(UTC).replace(tzinfo=None)
        (await db_session.get(Document, older)).created_at = now - timedelta(days=1)
        (await db_session.get(Document, newer)).created_at = now
        await add_warning(db_session, older, 1, "Image is too dark")
        await add_warning(db_session, newer, 1, "Image is too bright")

        response = await client.get(WARNINGS_PATH, headers=auth_headers)
        assert [entry["filename"] for entry in response.json()] == [
            "newer.pdf",
            "older.pdf",
        ]

    async def test_only_my_documents(
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
        """Test that only the user's own documents are listed in warnings"""
        
        mine = await upload_document(client, auth_headers, "mine.pdf")
        await add_warning(db_session, mine, 1, "Image is blurry")

        other_headers = await _other_headers(register, verify, login)
        theirs = await upload_document(client, other_headers, "theirs.pdf")
        await add_warning(db_session, theirs, 1, "Image is blurry")

        # Shared with me, but still theirs to rescan
        my_id = (await db_session.get(Document, mine)).owner_id
        db_session.add(DocumentShare(document_id=theirs, shared_with_user_id=my_id))
        await db_session.flush()

        mine_response = await client.get(WARNINGS_PATH, headers=auth_headers)
        assert [entry["filename"] for entry in mine_response.json()] == ["mine.pdf"]

        theirs_response = await client.get(WARNINGS_PATH, headers=other_headers)
        assert [entry["filename"] for entry in theirs_response.json()] == ["theirs.pdf"]

    async def test_trashed_document_warnings_are_hidden(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await upload_document(client, auth_headers, "scan.pdf")
        await add_warning(db_session, document_id, 1, "Image is blurry")
        document = await db_session.get(Document, document_id)
        document.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await db_session.flush()

        response = await client.get(WARNINGS_PATH, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []
