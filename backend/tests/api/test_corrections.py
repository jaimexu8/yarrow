"""correct extracted text, revert it, see it in search."""

import uuid

from sqlalchemy import select
from yarrow_db.models import Document, DocumentShare, Page, Region, RegionText, User

from tests.conftest import FIXTURES_DIR

UPLOAD = "/api/v1/documents/upload"
PARSED = "/api/v1/documents/{id}/parsed"
SEARCH = "/api/v1/search/"


def _text_url(document_id, region_id) -> str:
    return f"/api/v1/documents/{document_id}/regions/{region_id}/text"


def _revert_url(document_id, region_id) -> str:
    return f"/api/v1/documents/{document_id}/regions/{region_id}/correction"


async def _upload(client, headers, name="sample.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _add_text(
    db_session, document_id, text, page_number=1, reading_order=0, region_type=None
):
    """Seed one region with one text row and complete the document."""
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
    region = Region(
        id=uuid.uuid4(),
        page_id=page.id,
        reading_order=reading_order,
        region_type=region_type,
    )
    db_session.add_all([region, RegionText(region_id=region.id, text_content=text)])
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.page_count = max(document.page_count or 0, page_number)
    await db_session.flush()
    return region.id


async def _region_text(db_session, region_id):
    result = await db_session.execute(
        select(RegionText).where(RegionText.region_id == region_id)
    )
    return result.scalar_one()


async def _first_snippet(client, headers, term):
    response = await client.get(SEARCH, params={"q": term}, headers=headers)
    assert response.status_code == 200
    hits = response.json()
    if not hits or not hits[0]["snippets"]:
        return None
    return hits[0]["snippets"][0]


async def _shared_reader(
    client, db_session, register, verify, login, document_id, permission=None
):
    """Register, verify and log in a second user, then share the document."""
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    reader_id = (
        await db_session.execute(
            select(User.id).where(User.email == payload["email"])
        )
    ).scalar_one()
    db_session.add(
        DocumentShare(
            document_id=document_id, shared_with_user_id=reader_id, permission=permission
        )
    )
    await db_session.flush()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestDocumentTree:
    async def test_tree_carries_edit_fields(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "same all week")

        response = await client.get(PARSED.format(id=document_id), headers=auth_headers)
        assert response.status_code == 200
        tree = response.json()
        assert tree["can_edit"] is True
        region = tree["pages"][0]["regions"][0]
        assert region["text"] == "same all week"
        assert region["is_edited"] is False
        assert region["original_text"] is None


class TestCorrectRegionText:
    async def test_correction_is_saved_alongside_the_original(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "the quatrly summary")

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "the quarterly summary"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["changed"] is True

        row = await _region_text(db_session, region_id)
        assert row.text_content == "the quarterly summary"
        assert row.original_text == "the quatrly summary"

        tree = (
            await client.get(PARSED.format(id=document_id), headers=auth_headers)
        ).json()
        region = tree["pages"][0]["regions"][0]
        assert region["text"] == "the quarterly summary"
        assert region["is_edited"] is True
        assert region["original_text"] == "the quatrly summary"

    async def test_second_correction_keeps_the_first_original(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "original")
        correct = _text_url(document_id, region_id)

        await client.put(correct, json={"text": "first fix"}, headers=auth_headers)
        await client.put(correct, json={"text": "second fix"}, headers=auth_headers)

        row = await _region_text(db_session, region_id)
        assert row.text_content == "second fix"
        assert row.original_text == "original"

    async def test_saving_unchanged_text_changes_nothing(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "as extracted")

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "as extracted"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["changed"] is False

        row = await _region_text(db_session, region_id)
        assert row.original_text is None

    async def test_empty_replacement_is_accepted(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "garbled text")

        response = await client.put(
            _text_url(document_id, region_id), json={"text": ""}, headers=auth_headers
        )
        assert response.status_code == 200

        row = await _region_text(db_session, region_id)
        assert row.text_content == ""
        assert row.original_text == "garbled text"

    async def test_too_long_text_is_422(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "short")

        response = await client.put(
            _text_url(document_id, uuid.uuid4()),
            json={"text": "x" * 100_001},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_unknown_region_is_404(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _add_text(db_session, document_id, "assigned")

        response = await client.put(
            _text_url(document_id, uuid.uuid4()),
            json={"text": "never stored"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_region_of_another_document_is_404(
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
        theirs = await _upload(client, auth_headers, "secret.pdf")
        await _add_text(db_session, theirs, "not yours")
        leaked_region_id = (
            await db_session.execute(select(Region.id).where(Region.page_id.in_(
                select(Page.id).where(Page.document_id == theirs)
            )))
        ).scalar_one()

        mine = await _upload(client, auth_headers)
        await _add_text(db_session, mine, "mine")
        # A reviewer of `mine` may call the endpoint; the region id still
        # resolves only within the document in the path.
        reviewer = await _shared_reader(
            client, db_session, register, verify, login, mine, permission="review"
        )

        response = await client.put(
            _text_url(mine, leaked_region_id),
            json={"text": "exfiltrated"},
            headers=reviewer,
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "REGION_NOT_FOUND"

    async def test_table_region_is_not_editable(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(
            db_session, document_id, "table text", region_type="table"
        )

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "corrected"},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REGION_NOT_EDITABLE"

    async def test_region_without_text_row_is_not_editable(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "has text")
        await db_session.execute(
            RegionText.__table__.delete().where(RegionText.region_id == region_id)
        )
        await db_session.flush()

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "corrected"},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REGION_NOT_EDITABLE"

    async def test_pending_document_refuses_editing(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "text")
        document = await db_session.get(Document, document_id)
        document.status = "queued"
        await db_session.flush()

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "too early"},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DOCUMENT_NOT_PROCESSED"

    async def test_view_only_share_cannot_edit(
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
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "locked for others")
        reader = await _shared_reader(
            client, db_session, register, verify, login, document_id
        )

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "denied"},
            headers=reader,
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "DOCUMENT_READ_ONLY"

    async def test_review_share_can_edit(
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
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "please fix me")
        reader = await _shared_reader(
            client, db_session, register, verify, login, document_id, permission="review"
        )

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "corrected by reviewer"},
            headers=reader,
        )
        assert response.status_code == 200

        row = await _region_text(db_session, region_id)
        assert row.text_content == "corrected by reviewer"
        assert row.original_text == "please fix me"

    async def test_reviewer_can_undo(
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
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "raw text")
        await client.put(
            _text_url(document_id, region_id),
            json={"text": "owner fix"},
            headers=auth_headers,
        )

        reader = await _shared_reader(
            client, db_session, register, verify, login, document_id, permission="review"
        )
        response = await client.delete(
            _revert_url(document_id, region_id), headers=reader
        )
        assert response.status_code == 200

        row = await _region_text(db_session, region_id)
        assert row.text_content == "raw text"
        assert row.original_text is None

    async def test_requires_auth(self, client, fake_storage, fake_queue, db_session):
        response = await client.put(
            f"/api/v1/documents/{uuid.uuid4()}/regions/{uuid.uuid4()}/text",
            json={"text": "no token"},
        )
        assert response.status_code == 401


class TestUndoRegionCorrection:
    async def test_restore_the_raw_ocr_output(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "the rawn text")
        url = _text_url(document_id, region_id)
        await client.put(url, json={"text": "the raw text"}, headers=auth_headers)

        response = await client.delete(_revert_url(document_id, region_id), headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["changed"] is True

        tree = (
            await client.get(PARSED.format(id=document_id), headers=auth_headers)
        ).json()
        region = tree["pages"][0]["regions"][0]
        assert region["text"] == "the rawn text"
        assert region["is_edited"] is False
        assert region["original_text"] is None

    async def test_undo_without_a_correction_refuses(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "never touched")

        response = await client.delete(
            _revert_url(document_id, region_id), headers=auth_headers
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "NOT_CORRECTED"

    async def test_undo_edit_undo_edit_roundtrip(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "first extraction")
        url = _text_url(document_id, region_id)

        await client.put(url, json={"text": "fix one"}, headers=auth_headers)
        await client.delete(_revert_url(document_id, region_id), headers=auth_headers)
        await client.put(url, json={"text": "fix two"}, headers=auth_headers)

        row = await _region_text(db_session, region_id)
        assert row.text_content == "fix two"
        assert row.original_text == "first extraction"


class TestSearchReflectsCorrections:
    """us-96: the live text is matched, not the obsolete original."""

    async def test_corrected_text_matches_and_original_does_not(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "the quatrly summary")

        response = await client.put(
            _text_url(document_id, region_id),
            json={"text": "the quarterly summary"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # The corrected text is returned as an active match...
        snippet = await _first_snippet(client, auth_headers, "quarterly")
        assert "the quarterly summary" in snippet["text"]
        assert snippet["edited"] is True
        assert snippet["region_id"] == str(region_id)

        # ...and the replaced original text is no longer returned.
        assert await _first_snippet(client, auth_headers, "quatrly") is None

    async def test_undo_restores_the_original_text_to_search(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, document_id, "the quatrly summary")
        url = _text_url(document_id, region_id)

        await client.put(url, json={"text": "the quarterly summary"}, headers=auth_headers)
        response = await client.delete(_revert_url(document_id, region_id), headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["changed"] is True

        snippet = await _first_snippet(client, auth_headers, "quatrly")
        assert snippet is not None
        assert "the quatrly summary" in snippet["text"]
        assert snippet["edited"] is False
        assert await _first_snippet(client, auth_headers, "quarterly") is None

    async def test_snippet_reflects_the_correction(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        region_id = await _add_text(
            db_session, document_id, "the monthly report for June is late"
        )

        await client.put(
            _text_url(document_id, region_id),
            json={"text": "the quaterly reprot for June is late"},
            headers=auth_headers,
        )

        snippet = await _first_snippet(client, auth_headers, "reprot")
        assert snippet is not None
        assert snippet["text"] == "the quaterly reprot for June is late"

    async def test_a_stranger_does_not_see_other_users_corrections(
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
        theirs = await _upload(client, auth_headers)
        region_id = await _add_text(db_session, theirs, "the quatrly summary")
        await client.put(
            _text_url(theirs, region_id),
            json={"text": "the quarterly summary"},
            headers=auth_headers,
        )

        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        stranger = {"Authorization": f"Bearer {token['access_token']}"}

        assert await _first_snippet(client, stranger, "quarterly") is None
        assert await _first_snippet(client, stranger, "quatrly") is None
