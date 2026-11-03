"""US-103: user-created folders for organizing owned documents."""

import uuid

from tests.conftest import FIXTURES_DIR

FOLDERS = "/api/v1/folders/"
DOCUMENTS = "/api/v1/documents/"
UPLOAD = "/api/v1/documents/upload"


async def _upload(client, headers, name: str = "sample.pdf") -> str:
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    response = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (name, pdf, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["accepted"][0]["document_id"]


async def _other_headers(register, verify, login) -> dict[str, str]:
    payload, _ = await register()
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _create_folder(client, headers, name: str = "Invoices"):
    response = await client.post(FOLDERS, headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


class TestAuth:
    async def test_list_requires_auth(self, client):
        assert (await client.get(FOLDERS)).status_code == 401

    async def test_create_requires_auth(self, client):
        assert (await client.post(FOLDERS, json={"name": "A"})).status_code == 401

    async def test_rename_requires_auth(self, client):
        response = await client.patch(f"{FOLDERS}{uuid.uuid4()}", json={"name": "A"})
        assert response.status_code == 401

    async def test_delete_requires_auth(self, client):
        assert (await client.delete(f"{FOLDERS}{uuid.uuid4()}")).status_code == 401

    async def test_move_requires_auth(self, client):
        response = await client.patch(
            f"{DOCUMENTS}{uuid.uuid4()}/folder", json={"folder_id": None}
        )
        assert response.status_code == 401


class TestCreateAndList:
    async def test_create_then_list(self, client, auth_headers):
        created = await _create_folder(client, auth_headers, "Contracts")
        assert created["name"] == "Contracts"
        assert created["id"]

        listed = (await client.get(FOLDERS, headers=auth_headers)).json()
        assert [folder["name"] for folder in listed] == ["Contracts"]

    async def test_name_is_trimmed(self, client, auth_headers):
        created = await _create_folder(client, auth_headers, "  Taxes  ")
        assert created["name"] == "Taxes"

    async def test_duplicate_name_is_409(self, client, auth_headers):
        await _create_folder(client, auth_headers, "Invoices")
        response = await client.post(
            FOLDERS, headers=auth_headers, json={"name": "invoices"}
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    async def test_empty_name_is_422(self, client, auth_headers):
        response = await client.post(FOLDERS, headers=auth_headers, json={"name": "  "})
        assert response.status_code == 422

    async def test_cannot_see_others_folders(
        self, client, auth_headers, register, verify, login
    ):
        await _create_folder(client, auth_headers, "Mine")
        other = await _other_headers(register, verify, login)

        listed = (await client.get(FOLDERS, headers=other)).json()
        assert listed == []

        mine = (await client.get(FOLDERS, headers=auth_headers)).json()[0]
        rename = await client.patch(
            f"{FOLDERS}{mine['id']}", headers=other, json={"name": "Stolen"}
        )
        assert rename.status_code == 404
        delete = await client.delete(f"{FOLDERS}{mine['id']}", headers=other)
        assert delete.status_code == 404


class TestRename:
    async def test_rename_saves_the_new_name(self, client, auth_headers):
        folder = await _create_folder(client, auth_headers, "Old")
        response = await client.patch(
            f"{FOLDERS}{folder['id']}",
            headers=auth_headers,
            json={"name": "New"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New"

    async def test_rename_to_duplicate_is_409(self, client, auth_headers):
        await _create_folder(client, auth_headers, "Alpha")
        other = await _create_folder(client, auth_headers, "Beta")
        response = await client.patch(
            f"{FOLDERS}{other['id']}",
            headers=auth_headers,
            json={"name": "alpha"},
        )
        assert response.status_code == 409


class TestMoveAndFilter:
    async def test_move_into_folder_then_filter(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        filed = await _upload(client, auth_headers, "filed.pdf")
        await _upload(client, auth_headers, "unfiled.pdf")
        folder = await _create_folder(client, auth_headers, "Receipts")

        moved = await client.patch(
            f"{DOCUMENTS}{filed}/folder",
            headers=auth_headers,
            json={"folder_id": folder["id"]},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["folder_id"] == folder["id"]

        in_folder = (
            await client.get(
                DOCUMENTS, headers=auth_headers, params={"folder_id": folder["id"]}
            )
        ).json()
        assert [doc["filename"] for doc in in_folder] == ["filed.pdf"]

        leftover = (
            await client.get(
                DOCUMENTS, headers=auth_headers, params={"folder_id": "null"}
            )
        ).json()
        assert [doc["filename"] for doc in leftover] == ["unfiled.pdf"]

        everything = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        names = {doc["filename"] for doc in everything}
        assert names == {"filed.pdf", "unfiled.pdf"}

    async def test_move_into_someone_elses_folder_is_404(
        self, client, auth_headers, register, verify, login, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        other = await _other_headers(register, verify, login)
        their_folder = await _create_folder(client, other, "Theirs")

        response = await client.patch(
            f"{DOCUMENTS}{document_id}/folder",
            headers=auth_headers,
            json={"folder_id": their_folder["id"]},
        )
        assert response.status_code == 404

        listed = (await client.get(DOCUMENTS, headers=auth_headers)).json()
        assert listed[0]["folder_id"] is None

    async def test_filter_someone_elses_folder_is_404(
        self, client, auth_headers, register, verify, login
    ):
        other = await _other_headers(register, verify, login)
        their_folder = await _create_folder(client, other, "Theirs")
        response = await client.get(
            DOCUMENTS, headers=auth_headers, params={"folder_id": their_folder["id"]}
        )
        assert response.status_code == 404

    async def test_unfile_with_null(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers)
        folder = await _create_folder(client, auth_headers)
        await client.patch(
            f"{DOCUMENTS}{document_id}/folder",
            headers=auth_headers,
            json={"folder_id": folder["id"]},
        )

        response = await client.patch(
            f"{DOCUMENTS}{document_id}/folder",
            headers=auth_headers,
            json={"folder_id": None},
        )
        assert response.status_code == 200
        assert response.json()["folder_id"] is None


class TestDeleteFolder:
    async def test_delete_unfiles_documents(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        document_id = await _upload(client, auth_headers, "keep-me.pdf")
        folder = await _create_folder(client, auth_headers, "Temp")
        await client.patch(
            f"{DOCUMENTS}{document_id}/folder",
            headers=auth_headers,
            json={"folder_id": folder["id"]},
        )

        response = await client.delete(f"{FOLDERS}{folder['id']}", headers=auth_headers)
        assert response.status_code == 204

        listed = (await client.get(FOLDERS, headers=auth_headers)).json()
        assert listed == []

        document = (
            await client.get(f"{DOCUMENTS}{document_id}", headers=auth_headers)
        ).json()
        assert document["filename"] == "keep-me.pdf"
        assert document["folder_id"] is None

        unfiled = (
            await client.get(
                DOCUMENTS, headers=auth_headers, params={"folder_id": "null"}
            )
        ).json()
        assert [doc["filename"] for doc in unfiled] == ["keep-me.pdf"]
