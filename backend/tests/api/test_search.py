"""us-14/us-27/us-58: search extracted text and table cells, snippets, only the
caller's documents."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionTable,
    RegionText,
    Table,
    TableCell,
)

from tests.conftest import FIXTURES_DIR

SEARCH = "/api/v1/search/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers, name="sample.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        # The same fixture bytes are uploaded many times; not a duplicate test.
        data={"allow_duplicate": "true"},
        files=[("files", (name, pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _page(db_session, document_id, page_number):
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
    return page


async def _add_text(db_session, document_id, text, page_number=1, reading_order=0):
    page = await _page(db_session, document_id, page_number)
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


async def _add_corrected_text(db_session, document_id, corrected, original):
    """A region whose active text was manually corrected"""
    result = await db_session.execute(
        select(Page).where(Page.document_id == document_id, Page.page_number == 1)
    )
    page = result.scalar_one_or_none()
    if page is None:
        page = Page(id=uuid.uuid4(), document_id=document_id, page_number=1)
        db_session.add(page)
        await db_session.flush()
    region = Region(id=uuid.uuid4(), page_id=page.id, reading_order=0)
    row = RegionText(
        region_id=region.id, text_content=corrected, original_text=original
    )
    db_session.add_all([region, row])
    await db_session.flush()
    return region.id


class TestExactPhraseSearch:
    """quoted terms are exact phrases; unquoted words are AND terms."""

    async def _doc_with(
        self,
        client,
        auth_headers,
        fake_storage,
        fake_queue,
        db_session,
        text,
        name="phrase.pdf",
    ):
        document_id = await _upload(client, auth_headers, name)
        region_id = await _add_text(db_session, document_id, text)
        return document_id, region_id

    async def test_quoted_phrase_matches_contiguously(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, region_id = await self._doc_with(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "the quarterly summary is ready",
        )

        response = await client.get(
            SEARCH, params={"q": '"quarterly summary"'}, headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert [hit["id"] for hit in body] == [str(document_id)]
        snippet = body[0]["snippets"][0]
        assert snippet["region_id"] == str(region_id)
        # the whole phrase is highlighted, not just the first word
        assert (
            snippet["text"][snippet["match_start"] : snippet["match_end"]]
            == "quarterly summary"
        )

    async def test_quoted_phrase_rejects_interleaved_words(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await self._doc_with(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "quarterly report of the summary",
        )

        response = await client.get(
            SEARCH, params={"q": '"quarterly summary"'}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_quoted_phrase_rejects_reversed_words(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await self._doc_with(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "summary quarterly",
        )

        response = await client.get(
            SEARCH, params={"q": '"quarterly summary"'}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_unquoted_multi_word_matches_non_contiguous(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id, _ = await self._doc_with(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "summary of the quarterly report",
        )

        response = await client.get(
            SEARCH, params={"q": "quarterly summary"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert [hit["id"] for hit in response.json()] == [str(document_id)]

    async def test_phrase_and_word_and_semantics(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        # both the phrase and the extra word must be present
        await self._doc_with(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "the quarterly summary of 2024",
        )

        response = await client.get(
            SEARCH, params={"q": '"quarterly summary" 2024'}, headers=auth_headers
        )
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestOcrTolerance:
    """common OCR confusions (1/l/I, 0/O) and one-edit typos."""

    async def test_confusable_digits_match(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload_and_text(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "in 2O24 the numbers change",
        )

        response = await client.get(SEARCH, params={"q": "2024"}, headers=auth_headers)
        assert response.status_code == 200
        assert [hit["id"] for hit in response.json()] == [str(document_id)]
        snippet = response.json()[0]["snippets"][0]
        # the span covers the OCR'd text, so the UI can flag a near match
        assert snippet["text"][snippet["match_start"] : snippet["match_end"]] == "2O24"

    async def test_confusable_phrase_match(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _upload_and_text(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "section l.1 begins here",
        )

        response = await client.get(
            SEARCH, params={"q": '"section 1.1"'}, headers=auth_headers
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
        snippet = response.json()[0]["snippets"][0]
        assert (
            snippet["text"][snippet["match_start"] : snippet["match_end"]]
            == "section l.1"
        )

    async def test_fuzzy_one_edit_typo_matches(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        await _upload_and_text(
            client,
            auth_headers,
            fake_storage,
            fake_queue,
            db_session,
            "we recieve the goods",
        )

        response = await client.get(
            SEARCH, params={"q": "receive"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_corrected_region_matches_and_is_flagged(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers, "corrected.pdf")
        await _add_corrected_text(
            db_session,
            document_id,
            "the final result is 100",
            "the ffnal result is 100",
        )

        response = await client.get(
            SEARCH, params={"q": '"final result"'}, headers=auth_headers
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
        snippet = response.json()[0]["snippets"][0]
        assert snippet["edited"] is True

    async def test_query_longer_than_cap_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH, params={"q": "a" * 501}, headers=auth_headers
        )
        assert response.status_code == 422


async def _upload_and_text(
    client,
    auth_headers,
    fake_storage,
    fake_queue,
    db_session,
    text,
    name="tolerant.pdf",
):
    document_id = await _upload(client, auth_headers, name)
    await _add_text(db_session, document_id, text)
    return document_id


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

    async def test_trashed_document_is_hidden(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers, "notes.pdf")
        await _add_text(db_session, document_id, "the quarterly summary is ready")
        document = await db_session.get(Document, document_id)
        document.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await db_session.flush()

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


class TestSearchSort:
    """us-59: sort hits by upload date or filename."""

    async def _named_docs(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        """alpha.pdf older, zeta.pdf newer, so date and name orders differ."""
        older_id = await _upload(client, auth_headers, "alpha.pdf")
        await _add_text(db_session, older_id, "the quarterly summary is ready")
        newer_id = await _upload(client, auth_headers, "zeta.pdf")
        await _add_text(db_session, newer_id, "the quarterly summary is ready")
        await _set_meta(
            db_session,
            older_id,
            created_at=datetime(2025, 6, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None),
        )
        await _set_meta(
            db_session,
            newer_id,
            created_at=datetime(2025, 6, 3, 12, 0, tzinfo=UTC).replace(tzinfo=None),
        )
        return older_id, newer_id

    async def test_default_and_date_sort_newest_first(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        older_id, newer_id = await self._named_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        default = await client.get(
            SEARCH, params={"q": "quarterly"}, headers=auth_headers
        )
        by_date = await client.get(
            SEARCH,
            params={"q": "quarterly", "sort": "date"},
            headers=auth_headers,
        )
        newest_first = [str(newer_id), str(older_id)]
        assert [hit["id"] for hit in default.json()] == newest_first
        assert [hit["id"] for hit in by_date.json()] == newest_first

    async def test_name_sort_is_alphabetical(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        older_id, newer_id = await self._named_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "sort": "name"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        # alpha.pdf then zeta.pdf
        assert [hit["id"] for hit in response.json()] == [
            str(older_id),
            str(newer_id),
        ]

    async def test_browse_mode_uses_the_same_sort(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        older_id, newer_id = await self._named_docs(
            client, auth_headers, fake_storage, fake_queue, db_session
        )

        by_date = await client.get(
            SEARCH,
            params={"q": "", "date_from": "2025-01-01", "sort": "date"},
            headers=auth_headers,
        )
        by_name = await client.get(
            SEARCH,
            params={"q": "", "date_from": "2025-01-01", "sort": "name"},
            headers=auth_headers,
        )
        assert [hit["id"] for hit in by_date.json()] == [
            str(newer_id),
            str(older_id),
        ]
        assert [hit["id"] for hit in by_name.json()] == [
            str(older_id),
            str(newer_id),
        ]

    async def test_unknown_sort_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH,
            params={"q": "quarterly", "sort": "relevance"},
            headers=auth_headers,
        )
        assert response.status_code == 422


async def _add_table_part(
    db_session,
    document_id,
    rows,
    *,
    table_id=None,
    page_number=1,
    reading_order=0,
    row_start=0,
    col_start=0,
    header_rows=1,
    spans=None,
):
    """One RegionTable of a logical table. Cell rows are local to the part,
    as the worker writes them; returns the ids a search hit points at."""
    page = await _page(db_session, document_id, page_number)
    if table_id is None:
        table = Table(id=uuid.uuid4(), document_id=document_id)
        db_session.add(table)
        await db_session.flush()
        table_id = table.id
    region = Region(
        id=uuid.uuid4(),
        page_id=page.id,
        page_number=page_number,
        reading_order=reading_order,
        region_type="table",
    )
    region_table = RegionTable(
        id=uuid.uuid4(),
        region_id=region.id,
        table_id=table_id,
        reading_order=0,
        row_start=row_start,
        row_end=row_start + len(rows) - 1,
        col_start=col_start,
        col_end=col_start + max(len(row) for row in rows) - 1,
    )
    cells = [
        TableCell(
            id=uuid.uuid4(),
            region_table_id=region_table.id,
            row_idx=row_index,
            col_idx=col_index,
            row_span=1,
            col_span=(spans or {}).get((row_index, col_index), 1),
            text_content=value,
            is_header=row_index < header_rows,
        )
        for row_index, row in enumerate(rows)
        for col_index, value in enumerate(row)
        if value is not None
    ]
    db_session.add_all([region, region_table, *cells])
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.page_count = max(document.page_count or 0, page_number)
    await db_session.flush()
    return {
        "table_id": table_id,
        "region_table_id": region_table.id,
        "region_id": region.id,
        "cells": {(cell.row_idx, cell.col_idx): cell.id for cell in cells},
    }


async def _search(client, headers, **params):
    response = await client.get(SEARCH, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _matched(snippet):
    return snippet["text"][snippet["match_start"] : snippet["match_end"]]


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestTableCellSearch:
    async def test_cell_match_has_global_position_and_column_header(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers, "q3-report.pdf")
        first = await _add_table_part(
            db_session,
            document_id,
            [["Metric", "Value"], ["Gross margin", "41%"], ["Headcount", "120"]],
            page_number=1,
        )
        second = await _add_table_part(
            db_session,
            document_id,
            [["Operating cost", "880"], ["Net revenue", "1,204"]],
            table_id=first["table_id"],
            page_number=2,
            row_start=3,
            header_rows=0,
        )

        body = await _search(client, auth_headers, q="revenue")

        assert [hit["id"] for hit in body] == [str(document_id)]
        [snippet] = body[0]["snippets"]
        assert snippet["source"] == "table_cell"
        assert snippet["region_id"] == str(second["region_id"])
        assert snippet["page_number"] == 2
        assert snippet["text"] == "Net revenue"
        assert _matched(snippet) == "revenue"
        assert snippet["edited"] is False
        assert snippet["table_cell"] == {
            "table_id": str(first["table_id"]),
            "region_table_id": str(second["region_table_id"]),
            "cell_id": str(second["cells"][(1, 0)]),
            "row": 4,
            "col": 0,
            "row_span": 1,
            "col_span": 1,
            "is_header": False,
            "column_header": "Metric",
        }

    async def test_global_column_adds_the_part_column_offset(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        part = await _add_table_part(
            db_session,
            document_id,
            [["Region", "Total"], ["North", "quarterly 12"]],
            col_start=2,
        )

        body = await _search(client, auth_headers, q="quarterly")

        cell = body[0]["snippets"][0]["table_cell"]
        assert cell["cell_id"] == str(part["cells"][(1, 1)])
        assert (cell["row"], cell["col"]) == (1, 3)
        assert cell["column_header"] == "Total"

    async def test_column_header_comes_from_a_spanning_header(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session,
            document_id,
            [["Name", "Amounts", None], ["Rent", "100", "quarterly fee"]],
            spans={(0, 1): 2},
        )

        body = await _search(client, auth_headers, q="quarterly")

        cell = body[0]["snippets"][0]["table_cell"]
        assert cell["col"] == 2
        assert cell["column_header"] == "Amounts"

    async def test_header_cell_match_has_no_column_header(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session,
            document_id,
            [["Quarterly total", "Notes"], ["10", "fine"]],
        )

        body = await _search(client, auth_headers, q="quarterly")

        cell = body[0]["snippets"][0]["table_cell"]
        assert cell["is_header"] is True
        assert cell["column_header"] is None

    async def test_cell_without_header_has_no_column_header(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session, document_id, [["quarterly", "10"]], header_rows=0
        )

        body = await _search(client, auth_headers, q="quarterly")

        assert body[0]["snippets"][0]["table_cell"]["column_header"] is None

    async def test_phrase_query_inside_a_cell(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        part = await _add_table_part(
            db_session,
            document_id,
            [["Line", "Item"], ["1", "net operating revenue"]],
        )

        body = await _search(client, auth_headers, q='"operating revenue"')
        snippet = body[0]["snippets"][0]
        assert snippet["table_cell"]["cell_id"] == str(part["cells"][(1, 1)])
        assert _matched(snippet) == "operating revenue"

        assert await _search(client, auth_headers, q='"revenue operating"') == []

    async def test_all_terms_must_match_the_same_cell(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session, document_id, [["Metric", "Value"], ["revenue", "quarterly"]]
        )

        assert await _search(client, auth_headers, q="revenue quarterly") == []

    async def test_ocr_confusable_inside_a_cell(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session, document_id, [["Year", "Total"], ["2O24", "1,204"]]
        )

        body = await _search(client, auth_headers, q="2024")

        snippet = body[0]["snippets"][0]
        assert snippet["source"] == "table_cell"
        assert _matched(snippet) == "2O24"

    async def test_long_cell_text_is_trimmed_like_region_snippets(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(
            db_session,
            document_id,
            [["Notes"], [("a" * 80) + " quarterly " + ("b" * 80)]],
        )

        body = await _search(client, auth_headers, q="quarterly")

        snippet = body[0]["snippets"][0]
        assert snippet["text"].startswith("...")
        assert snippet["text"].endswith("...")
        assert _matched(snippet) == "quarterly"

    async def test_text_snippets_keep_text_source(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "the quarterly summary")

        body = await _search(client, auth_headers, q="quarterly")

        snippet = body[0]["snippets"][0]
        assert snippet["source"] == "text"
        assert snippet["table_cell"] is None

    async def test_scope_text_excludes_cells(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        paragraph = await _add_text(db_session, document_id, "quarterly plan")
        await _add_table_part(
            db_session, document_id, [["Metric"], ["quarterly cost"]], reading_order=1
        )

        body = await _search(client, auth_headers, q="quarterly", scope="text")

        assert [row["region_id"] for row in body[0]["snippets"]] == [str(paragraph)]
        assert [row["source"] for row in body[0]["snippets"]] == ["text"]

    async def test_scope_tables_excludes_paragraphs(
        self, client, auth_headers, db_session
    ):
        text_only = await _upload(client, auth_headers, "text-only.pdf")
        await _add_text(db_session, text_only, "quarterly plan")
        with_table = await _upload(client, auth_headers, "with-table.pdf")
        await _add_text(db_session, with_table, "quarterly plan")
        part = await _add_table_part(
            db_session, with_table, [["Metric"], ["quarterly cost"]], reading_order=1
        )

        body = await _search(client, auth_headers, q="quarterly", scope="tables")

        assert [hit["id"] for hit in body] == [str(with_table)]
        assert [row["source"] for row in body[0]["snippets"]] == ["table_cell"]
        assert body[0]["snippets"][0]["region_id"] == str(part["region_id"])

    async def test_scope_all_is_the_default(self, client, auth_headers, db_session):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "quarterly plan")
        await _add_table_part(
            db_session, document_id, [["Metric"], ["quarterly cost"]], reading_order=1
        )

        default = await _search(client, auth_headers, q="quarterly")
        explicit = await _search(client, auth_headers, q="quarterly", scope="all")

        assert [row["source"] for row in default[0]["snippets"]] == [
            "text",
            "table_cell",
        ]
        assert explicit == default

    async def test_unknown_scope_is_422(self, client, auth_headers):
        response = await client.get(
            SEARCH, params={"q": "quarterly", "scope": "cells"}, headers=auth_headers
        )
        assert response.status_code == 422

    async def test_other_users_cells_are_never_returned(
        self, client, auth_headers, register, verify, login, db_session
    ):
        other = await _other_headers(register, verify, login)
        theirs = await _upload(client, other, "secret.pdf")
        await _add_table_part(db_session, theirs, [["Metric"], ["quarterly cost"]])

        for scope in ("all", "tables"):
            assert (
                await _search(client, auth_headers, q="quarterly", scope=scope) == []
            )
        assert len(await _search(client, other, q="quarterly", scope="tables")) == 1

    async def test_trashed_document_cells_are_excluded(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(db_session, document_id, [["Metric"], ["quarterly"]])
        document = await db_session.get(Document, document_id)
        document.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await db_session.flush()

        assert await _search(client, auth_headers, q="quarterly", scope="tables") == []

    async def test_filters_apply_to_cell_matches(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_table_part(db_session, document_id, [["Metric"], ["quarterly"]])

        matching = await _search(
            client, auth_headers, q="quarterly", file_type="application/pdf"
        )
        other_type = await _search(
            client, auth_headers, q="quarterly", file_type="image/png"
        )

        assert [hit["id"] for hit in matching] == [str(document_id)]
        assert other_type == []

    async def test_text_and_cell_snippets_are_interleaved_in_reading_order(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        intro = await _add_text(
            db_session, document_id, "quarterly intro", page_number=1, reading_order=0
        )
        part = await _add_table_part(
            db_session,
            document_id,
            [["Metric", "Note"], ["quarterly a", "x"], ["y", "quarterly b"]],
            page_number=1,
            reading_order=1,
        )
        outro = await _add_text(
            db_session, document_id, "quarterly outro", page_number=1, reading_order=2
        )
        later = await _add_text(
            db_session, document_id, "quarterly later", page_number=2, reading_order=0
        )

        body = await _search(client, auth_headers, q="quarterly")

        assert [
            (row["region_id"], (row["table_cell"] or {}).get("cell_id"))
            for row in body[0]["snippets"]
        ] == [
            (str(intro), None),
            (str(part["region_id"]), str(part["cells"][(1, 0)])),
            (str(part["region_id"]), str(part["cells"][(2, 1)])),
            (str(outro), None),
            (str(later), None),
        ]

    async def test_snippet_cap_holds_across_both_sources(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        for order in range(3):
            await _add_text(
                db_session, document_id, f"quarterly {order}", reading_order=order * 2
            )
        await _add_table_part(
            db_session,
            document_id,
            [["Metric"], ["quarterly a"], ["quarterly b"], ["quarterly c"]],
            reading_order=1,
        )

        body = await _search(client, auth_headers, q="quarterly")

        snippets = body[0]["snippets"]
        assert len(snippets) == 5
        assert [row["source"] for row in snippets] == [
            "text",
            "table_cell",
            "table_cell",
            "table_cell",
            "text",
        ]

    async def test_documents_keep_the_requested_sort_across_sources(
        self, client, auth_headers, db_session
    ):
        zeta = await _upload(client, auth_headers, "zeta.pdf")
        await _add_text(db_session, zeta, "quarterly plan")
        alpha = await _upload(client, auth_headers, "alpha.pdf")
        await _add_table_part(db_session, alpha, [["Metric"], ["quarterly cost"]])

        by_name = await _search(client, auth_headers, q="quarterly", sort="name")

        assert [hit["id"] for hit in by_name] == [str(alpha), str(zeta)]

    async def test_parsed_tree_exposes_the_matched_cell_id(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        part = await _add_table_part(
            db_session, document_id, [["Metric"], ["quarterly cost"]]
        )

        body = await _search(client, auth_headers, q="quarterly")
        cell_id = body[0]["snippets"][0]["table_cell"]["cell_id"]
        parsed = await client.get(
            f"/api/v1/documents/{document_id}/parsed", headers=auth_headers
        )

        assert parsed.status_code == 200, parsed.text
        [table] = parsed.json()["tables"]
        by_id = {cell["id"]: cell for cell in table["cells"]}
        assert set(by_id) == {str(cell) for cell in part["cells"].values()}
        assert by_id[cell_id]["text"] == "quarterly cost"
