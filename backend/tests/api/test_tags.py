"""US-105: users tag their own documents and filter by tag."""

import uuid

from sqlalchemy import select
from yarrow_db.models import DocumentTag, Tag

from tests.conftest import FIXTURES_DIR

DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"
TAGS = "/api/v1/tags/"


async def _upload(client, headers, name: str = "sample.pdf") -> str:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["accepted"][0]["document_id"]


async def _other_user_headers(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


class TestAddTag:
    async def test_requires_auth(self, client):
        response = await client.post(
            f"{DOCUMENTS}{uuid.uuid4()}/tags", json={"name": "invoice"}
        )
        assert response.status_code == 401

    async def test_owner_sees_the_tag_on_the_document(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)

        response = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=auth_headers,
            json={"name": "  Invoice  "},
        )

        assert response.status_code == 200, response.text
        tags = response.json()["tags"]
        assert [tag["name"] for tag in tags] == ["Invoice"]

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library[0]["id"] == document_id
        assert [tag["name"] for tag in library[0]["tags"]] == ["Invoice"]

        mine = (await client.get(TAGS, headers=auth_headers)).json()
        assert [(tag["name"], tag["document_count"]) for tag in mine] == [
            ("Invoice", 1)
        ]

    async def test_same_name_is_reused_ignoring_case(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers, "a.pdf")
        second = await _upload(client, auth_headers, "b.pdf")

        added = await client.post(
            f"{DOCUMENTS}{first}/tags",
            headers=auth_headers,
            json={"name": "Invoice"},
        )
        reused = await client.post(
            f"{DOCUMENTS}{second}/tags",
            headers=auth_headers,
            json={"name": "invoice"},
        )

        first_id = added.json()["tags"][0]["id"]
        assert reused.json()["tags"][0]["id"] == first_id
        assert reused.json()["tags"][0]["name"] == "Invoice"

        mine = (await client.get(TAGS, headers=auth_headers)).json()
        assert [(tag["name"], tag["document_count"]) for tag in mine] == [
            ("Invoice", 2)
        ]

    async def test_empty_name_is_rejected(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        response = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=auth_headers,
            json={"name": "   "},
        )
        assert response.status_code == 422

    async def test_cannot_tag_someone_elses_document(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        other = await _other_user_headers(register, verify, login)

        response = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=other,
            json={"name": "shared"},
        )
        assert response.status_code == 404

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library[0]["tags"] == []


class TestFilterByTag:
    async def test_requires_auth(self, client):
        response = await client.get(DOCUMENTS, params={"tag_id": str(uuid.uuid4())})
        assert response.status_code == 401

    async def test_only_documents_with_that_tag(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        tagged = await _upload(client, auth_headers, "tagged.pdf")
        await _upload(client, auth_headers, "plain.pdf")
        added = await client.post(
            f"{DOCUMENTS}{tagged}/tags",
            headers=auth_headers,
            json={"name": "tax"},
        )
        tag_id = added.json()["tags"][0]["id"]

        filtered = (
            await client.get(DOCUMENTS, headers=auth_headers, params={"tag_id": tag_id})
        ).json()
        assert [doc["filename"] for doc in filtered] == ["tagged.pdf"]
        assert filtered[0]["tags"][0]["name"] == "tax"

        all_docs = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert sorted(doc["filename"] for doc in all_docs) == [
            "plain.pdf",
            "tagged.pdf",
        ]

    async def test_unknown_tag_returns_no_documents(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        await _upload(client, auth_headers)
        filtered = await client.get(
            DOCUMENTS, headers=auth_headers, params={"tag_id": str(uuid.uuid4())}
        )
        assert filtered.status_code == 200
        assert filtered.json() == []

    async def test_other_users_tag_does_not_match(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        added = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=auth_headers,
            json={"name": "mine"},
        )
        tag_id = added.json()["tags"][0]["id"]
        other = await _other_user_headers(register, verify, login)

        filtered = await client.get(
            DOCUMENTS, headers=other, params={"tag_id": tag_id}
        )
        assert filtered.json() == []
        assert (await client.get(TAGS, headers=other)).json() == []


class TestRemoveTag:
    async def test_requires_auth(self, client):
        response = await client.delete(
            f"{DOCUMENTS}{uuid.uuid4()}/tags/{uuid.uuid4()}"
        )
        assert response.status_code == 401

    async def test_tag_is_gone_after_remove(
        self, client, auth_headers, fake_storage, fake_queue, db_session
    ):
        document_id = await _upload(client, auth_headers)
        added = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=auth_headers,
            json={"name": "draft"},
        )
        tag_id = added.json()["tags"][0]["id"]

        removed = await client.delete(
            f"{DOCUMENTS}{document_id}/tags/{tag_id}", headers=auth_headers
        )
        assert removed.status_code == 204

        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert library[0]["tags"] == []
        assert (await client.get(TAGS, headers=auth_headers)).json() == []

        leftover = await db_session.scalar(
            select(Tag.id).where(Tag.id == uuid.UUID(tag_id))
        )
        assert leftover is None
        links = await db_session.scalar(
            select(DocumentTag.document_id).where(
                DocumentTag.document_id == uuid.UUID(document_id)
            )
        )
        assert links is None

    async def test_removing_from_one_document_keeps_the_tag_on_another(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        first = await _upload(client, auth_headers, "a.pdf")
        second = await _upload(client, auth_headers, "b.pdf")
        for document_id in (first, second):
            await client.post(
                f"{DOCUMENTS}{document_id}/tags",
                headers=auth_headers,
                json={"name": "keep"},
            )
        tag_id = (
            await client.get(TAGS, headers=auth_headers)
        ).json()[0]["id"]

        await client.delete(
            f"{DOCUMENTS}{first}/tags/{tag_id}", headers=auth_headers
        )

        library = {
            doc["filename"]: [tag["name"] for tag in doc["tags"]]
            for doc in (await client.get(DOCUMENTS, headers=auth_headers)).json()
        }
        assert library["a.pdf"] == []
        assert library["b.pdf"] == ["keep"]
        mine = (await client.get(TAGS, headers=auth_headers)).json()
        assert [(tag["name"], tag["document_count"]) for tag in mine] == [
            ("keep", 1)
        ]

    async def test_cannot_remove_from_someone_elses_document(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        added = await client.post(
            f"{DOCUMENTS}{document_id}/tags",
            headers=auth_headers,
            json={"name": "secret"},
        )
        tag_id = added.json()["tags"][0]["id"]
        other = await _other_user_headers(register, verify, login)

        response = await client.delete(
            f"{DOCUMENTS}{document_id}/tags/{tag_id}", headers=other
        )
        assert response.status_code == 404
        library = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert [tag["name"] for tag in library[0]["tags"]] == ["secret"]


class TestListTags:
    async def test_requires_auth(self, client):
        assert (await client.get(TAGS)).status_code == 401

    async def test_new_user_has_no_tags(self, client, auth_headers):
        response = await client.get(TAGS, headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []
