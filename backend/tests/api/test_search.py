"""us-14/us-27: search extracted text, snippets, only the caller's documents."""

import uuid

from sqlalchemy import select
from yarrow_db.models import Document, Page, Region, RegionText

from tests.conftest import FIXTURES_DIR

SEARCH = "/api/v1/search/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers, name="sample.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _add_text(db_session, document_id, text, page_number=1, reading_order=0):
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
    region = Region(id=uuid.uuid4(), page_id=page.id, reading_order=reading_order)
    db_session.add_all(
        [region, RegionText(region_id=region.id, text_content=text)]
    )
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.page_count = max(document.page_count or 0, page_number)
    await db_session.flush()
    return region.id


async def _other_headers(register, verify, login):
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestSearch:
    async def test_requires_auth(self, client):
        assert (await client.get(SEARCH, params={"q": "hello"})).status_code == 401

    async def test_blank_query_is_empty(self, client, auth_headers):
        response = await client.get(SEARCH, params={"q": "  "}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_match_returns_my_document(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers, "notes.pdf")
        region_id = await _add_text(
            db_session, document_id, "the quarterly summary is ready"
        )

        response = await client.get(
            SEARCH, params={"q": "quarterly"}, headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert [hit["filename"] for hit in body] == ["notes.pdf"]
        assert body[0]["id"] == str(document_id)
        snippet = body[0]["snippets"][0]
        assert snippet["region_id"] == str(region_id)
        assert snippet["page_number"] == 1
        assert (
            snippet["text"][snippet["match_start"] : snippet["match_end"]].lower()
            == "quarterly"
        )

    async def test_no_match_is_empty(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "the quarterly summary is ready")

        response = await client.get(
            SEARCH, params={"q": "zebra"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_other_users_document_is_hidden(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue, db_session
    ):
        other = await _other_headers(register, verify, login)
        theirs = await _upload(client, other, "secret.pdf")
        await _add_text(db_session, theirs, "the quarterly summary is ready")

        response = await client.get(
            SEARCH, params={"q": "quarterly"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_snippets_point_at_each_matching_region(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers, "notes.pdf")
        first = await _add_text(
            db_session, document_id, "quarterly plan", page_number=1, reading_order=0
        )
        second = await _add_text(
            db_session,
            document_id,
            "later quarterly review",
            page_number=2,
            reading_order=0,
        )

        response = await client.get(
            SEARCH, params={"q": "quarterly"}, headers=auth_headers
        )
        assert response.status_code == 200
        snippets = response.json()[0]["snippets"]
        assert [row["region_id"] for row in snippets] == [str(first), str(second)]
        assert [row["page_number"] for row in snippets] == [1, 2]

    async def test_long_snippet_is_trimmed(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers, "notes.pdf")
        await _add_text(
            db_session, document_id, ("a" * 80) + " quarterly " + ("b" * 80)
        )

        response = await client.get(
            SEARCH, params={"q": "quarterly"}, headers=auth_headers
        )
        snippet = response.json()[0]["snippets"][0]
        assert snippet["text"].startswith("...")
        assert snippet["text"].endswith("...")
        assert (
            snippet["text"][snippet["match_start"] : snippet["match_end"]].lower()
            == "quarterly"
        )
