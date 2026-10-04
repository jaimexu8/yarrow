"""US-39: renaming a document changes only its display name."""

import uuid

import pytest

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers) -> str:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", ("sample.pdf", pdf, "application/pdf"))],
    )
    return response.json()["accepted"][0]["document_id"]


async def _other_user_headers(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _names(client, headers) -> list[str]:
    library = (await client.get(DOCUMENTS, headers=headers)).json()
    return [document["filename"] for document in library]


class TestRename:
    async def test_new_name_is_saved(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": "Q3 invoices.pdf"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "Q3 invoices.pdf"

    async def test_library_shows_the_new_name(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": "Renamed.pdf"},
        )

        assert await _names(client, auth_headers) == ["Renamed.pdf"]
        detail = await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        assert detail.json()["filename"] == "Renamed.pdf"

    async def test_surrounding_spaces_are_trimmed(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": "  Report.pdf  "},
        )

        assert response.json()["filename"] == "Report.pdf"

    async def test_stored_file_is_untouched(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        """Storage is keyed by id, so a rename moves nothing."""
        document_id = await _upload(client, auth_headers)
        keys_before = set(fake_storage.objects)

        await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": "Other.pdf"},
        )

        assert set(fake_storage.objects) == keys_before


class TestInvalidRename:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "a" * 256,
            "folder/file.pdf",
            "folder\\file.pdf",
            "line\nbreak.pdf",
        ],
    )
    async def test_invalid_name_keeps_the_old_one(
        self, client, auth_headers, fake_storage, fake_queue, name
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=auth_headers,
            json={"filename": name},
        )

        assert response.status_code == 422
        assert await _names(client, auth_headers) == ["sample.pdf"]

    async def test_missing_name_is_rejected(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.patch(
            f"{DOCUMENTS}{document_id}", headers=auth_headers, json={}
        )

        assert response.status_code == 422
        assert await _names(client, auth_headers) == ["sample.pdf"]


class TestOwnership:
    async def test_requires_auth(self, client):
        response = await client.patch(
            f"{DOCUMENTS}{uuid.uuid4()}", json={"filename": "x.pdf"}
        )
        assert response.status_code == 401

    async def test_someone_elses_document_is_404_and_unchanged(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
    ):
        document_id = await _upload(client, auth_headers)
        other = await _other_user_headers(register, verify, login)

        response = await client.patch(
            f"{DOCUMENTS}{document_id}",
            headers=other,
            json={"filename": "Mine now.pdf"},
        )

        assert response.status_code == 404
        assert await _names(client, auth_headers) == ["sample.pdf"]

    async def test_unknown_document_is_404(self, client, auth_headers):
        response = await client.patch(
            f"{DOCUMENTS}{uuid.uuid4()}",
            headers=auth_headers,
            json={"filename": "x.pdf"},
        )
        assert response.status_code == 404
