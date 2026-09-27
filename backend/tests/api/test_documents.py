import pytest


@pytest.mark.asyncio
async def test_list_documents_empty(client, auth_headers):
    response = await client.get("/api/v1/documents/", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_documents_only_own(
    client, user, other_user, auth_headers, add_document
):
    mine = await add_document(user.id, "mine.pdf")
    await add_document(other_user.id, "theirs.pdf")

    response = await client.get("/api/v1/documents/", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert [doc["id"] for doc in body] == [str(mine.id)]
    assert body[0]["filename"] == "mine.pdf"


@pytest.mark.asyncio
async def test_list_documents_requires_login(client):
    response = await client.get("/api/v1/documents/")
    assert response.status_code == 401
