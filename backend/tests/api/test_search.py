"""us-14/us-27: search extracted text, snippets, only the caller's documents."""

import uuid
from datetime import UTC, datetime

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
    db_session.add_all([region, RegionText(region_id=region.id, text_content=text)])
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


async def _set_meta(db_session, document_id, **fields):
    document = await db_session.get(Document, document_id)
    for name, value in fields.items():
        setattr(document, name, value)
    await db_session.flush()


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

        response = await client.get(SEARCH, params={"q": "zebra"}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

    async def test_other_users_document_is_hidden(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
        db_session,
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


class TestSearchFilters:
    """us-15: filter search results by date range and document type."""

    async def _two_typed_docs(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """A pdf and a png (file_type set directly) sharing the same text."""
        pdf_id = await _upload(client, auth_headers, "notes.pdf")
        await _add_text(db_session, pdf_id, "the quarterly summary is ready")
        png_id = await _upload(client, auth_headers, "scan.pdf")
        await _add_text(db_session, png_id, "the quarterly summary is ready")
        await _set_meta(db_session, png_id, file_type="image/png")
        return pdf_id, png_id

    async def _dated_docs(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """Three documents on 2025-06-01 (late), 2025-06-02 (early) and
        2025-06-03, all matching 'quarterly'."""
        ids = []
        for _ in range(3):
            document_id = await _upload(client, auth_headers)
            await _add_text(db_session, document_id, "the quarterly summary is ready")
            ids.append(document_id)
        # created_at is a naive-UTC column
        moments = [
            datetime(2025, 6, 1, 23, 0, tzinfo=UTC),
            datetime(2025, 6, 2, 0, 30, tzinfo=UTC),
            datetime(2025, 6, 3, 12, 0, tzinfo=UTC),
        ]
        for document_id, moment in zip(ids, moments):
            await _set_meta(
                db_session, document_id, created_at=moment.replace(tzinfo=None)
            )
        return ids

    async def test_file_type_filter(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        pdf_id, png_id = await self._two_typed_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "file_type": "image/png"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert [hit["id"] for hit in response.json()] == [str(png_id)]

        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "file_type": "application/pdf"},
            headers=auth_headers,
        )
        assert [hit["id"] for hit in response.json()] == [str(pdf_id)]

    async def test_date_from_excludes_older_documents(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        ids = await self._dated_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "date_from": "2025-06-02"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        # newest first
        assert [hit["id"] for hit in response.json()] == [str(ids[2]), str(ids[1])]

    async def test_date_to_is_inclusive(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        ids = await self._dated_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "date_to": "2025-06-01"},
            headers=auth_headers,
        )
        # the 2025-06-01 doc is in; the 2025-06-02 00:30 doc is out
        assert [hit["id"] for hit in response.json()] == [str(ids[0])]

    async def test_date_range(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        ids = await self._dated_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={
                "q": "quarterly",
                "date_from": "2025-06-01",
                "date_to": "2025-06-01",
            },
            headers=auth_headers,
        )
        assert [hit["id"] for hit in response.json()] == [str(ids[0])]

    async def test_browse_mode_returns_documents_without_snippets(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        pdf_id, png_id = await self._two_typed_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={"q": "", "file_type": "image/png"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert [hit["id"] for hit in body] == [str(png_id)]
        assert body[0]["snippets"] == []

        response = await client.get(
            SEARCH,
            params={"q": "", "date_to": "2025-06-01"},
            headers=auth_headers,
        )
        # both docs default to now (2026), so none fall on or before 2025-06-01
        assert response.json() == []

        response = await client.get(
            SEARCH,
            params={"q": "", "file_type": "application/pdf"},
            headers=auth_headers,
        )
        assert [hit["id"] for hit in response.json()] == [str(pdf_id)]

    async def test_all_three_filters_combined(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        _, png_id = await self._two_typed_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )
        await _set_meta(
            db_session,
            png_id,
            created_at=datetime(2025, 6, 3, 12, 0, tzinfo=UTC).replace(tzinfo=None),
        )

        response = await client.get(
            SEARCH,
            params={
                "q": "quarterly",
                "file_type": "image/png",
                "date_from": "2025-06-01",
                "date_to": "2025-06-30",
            },
            headers=auth_headers,
        )
        assert [hit["id"] for hit in response.json()] == [str(png_id)]

        response = await client.get(
            SEARCH,
            params={
                "q": "quarterly",
                "file_type": "image/png",
                "date_to": "2025-05-31",
            },
            headers=auth_headers,
        )
        assert response.json() == []

    async def test_invalid_file_type_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "file_type": "text/plain"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_date_from_after_date_to_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH,
            params={
                "q": "quarterly",
                "date_from": "2025-06-02",
                "date_to": "2025-06-01",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_malformed_date_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "date_from": "not-a-date"},
            headers=auth_headers,
        )
        assert response.status_code == 422
