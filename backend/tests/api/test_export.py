"""GET /documents/{id}/export and /markdown: downloading the extracted content (US-16)."""

import json
import uuid

import pytest
from yarrow_db.models import Document, Page, Region, RegionText

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


def _pdf():
    return (
        "files",
        ("sample.pdf", (FIXTURES_DIR / "sample.pdf").read_bytes(), "application/pdf"),
    )


async def _upload(client, headers) -> uuid.UUID:
    response = await client.post(UPLOAD, headers=headers, files=[_pdf()])
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["accepted"][0]["document_id"])


async def _owner_id(client, headers) -> uuid.UUID:
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return uuid.UUID(me["id"])


async def _complete(db_session, document_id: uuid.UUID) -> None:
    """Mark the document finished and give it two pages with regions.

    The worker is not running in the tests, so the parsed rows are written
    directly. Page 1 has a heading and a list; page 2 a plain paragraph.
    """
    document = await db_session.get(Document, document_id)
    document.status = "completed"
    document.page_count = 2

    page1 = Page(id=uuid.uuid4(), document_id=document_id, page_number=1)
    page2 = Page(id=uuid.uuid4(), document_id=document_id, page_number=2)
    heading = Region(
        id=uuid.uuid4(), page_id=page1.id, reading_order=0, region_type="header"
    )
    paragraph = Region(
        id=uuid.uuid4(), page_id=page1.id, reading_order=1, region_type="paragraph"
    )
    body = Region(
        id=uuid.uuid4(), page_id=page2.id, reading_order=0, region_type="paragraph"
    )
    db_session.add_all(
        [
            page1,
            page2,
            heading,
            paragraph,
            body,
            RegionText(region_id=heading.id, text_content="Quarterly Report"),
            RegionText(
                region_id=paragraph.id,
                text_content="• first item\n• second item\n2024 was a strong year.",
            ),
            RegionText(region_id=body.id, text_content="Second page body."),
        ]
    )
    await db_session.flush()


async def _direct_document(
    db_session, owner_id: uuid.UUID, filename: str, status: str
) -> uuid.UUID:
    document = Document(
        id=uuid.uuid4(),
        owner_id=owner_id,
        filename=filename,
        file_size_bytes=10,
        file_type="application/pdf",
        storage_key=f"documents/{uuid.uuid4()}/original",
        status=status,
    )
    db_session.add(document)
    await db_session.flush()
    return document.id


@pytest.mark.usefixtures("fake_storage", "fake_queue")
class TestExport:
    async def test_markdown_download(self, client, auth_headers, db_session):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "markdown"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        body = response.text
        assert "# Quarterly Report" in body
        assert "- first item" in body
        assert "- second item" in body
        assert "2024 was a strong year." in body
        assert "Second page body." in body
        assert "attachment" in response.headers["content-disposition"]
        assert 'filename="sample.md"' in response.headers["content-disposition"]

    async def test_plain_text_download(self, client, auth_headers, db_session):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "text"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        body = response.text
        assert "Quarterly Report\n" in body
        assert "=====" in body
        assert "#" not in body
        assert "|" not in body
        assert 'filename="sample.txt"' in response.headers["content-disposition"]

    async def test_json_download(self, client, auth_headers, db_session):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "json"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/json"
        payload = json.loads(response.text)
        assert payload["schema_version"] == 1
        assert payload["pages_included"] == [1, 2]
        assert payload["stats"]["region_count"] == 3
        assert payload["pages"][0]["regions"][0]["text"] == "Quarterly Report"
        assert 'filename="sample.json"' in response.headers["content-disposition"]

    async def test_unprocessed_document_is_a_conflict(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "markdown"},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DOCUMENT_NOT_PROCESSED"

    @pytest.mark.parametrize("params", [{}, {"format": "csv"}])
    async def test_missing_or_unknown_format_is_rejected(
        self, client, auth_headers, db_session, params
    ):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export", headers=auth_headers, params=params
        )

        assert response.status_code == 422

    async def test_other_users_cannot_export(
        self,
        client,
        auth_headers,
        db_session,
        register,
        verify,
        login,
    ):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        payload, _ = await register()
        await verify(payload["email"])
        token = (await login(payload["email"], payload["password"])).json()
        other = {"Authorization": f"Bearer {token['access_token']}"}

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=other,
            params={"format": "markdown"},
        )

        assert response.status_code == 404

    async def test_page_filter_limits_the_download(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "json", "page": 1},
        )

        assert response.status_code == 200, response.text
        payload = json.loads(response.text)
        assert payload["pages_included"] == [1]
        assert "Second page body." not in response.text

    async def test_export_requires_authentication(self, client):
        document_id = uuid.uuid4()

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export", params={"format": "markdown"}
        )

        assert response.status_code == 401

    async def test_markdown_endpoint_serves_markdown(
        self, client, auth_headers, db_session
    ):
        document_id = await _upload(client, auth_headers)
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/markdown", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "# Quarterly Report" in response.text
        assert "Second page body." in response.text

    async def test_unicode_filename_keeps_its_characters(
        self, client, auth_headers, db_session
    ):
        owner_id = await _owner_id(client, auth_headers)
        document_id = await _direct_document(
            db_session, owner_id, "Berík report.pdf", "completed"
        )
        await _complete(db_session, document_id)

        response = await client.get(
            f"{DOCUMENTS}{document_id}/export",
            headers=auth_headers,
            params={"format": "markdown"},
        )

        assert response.status_code == 200, response.text
        disposition = response.headers["content-disposition"]
        assert 'filename="Ber-k-report.md"' in disposition
        assert "filename*=UTF-8''Ber%C3%ADk%20report.md" in disposition
