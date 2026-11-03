"""us-102: admin delete of another user's account."""

import uuid

from sqlalchemy import func, select
from yarrow_db.models import Document, User

from tests.conftest import FIXTURES_DIR

ACCOUNTS = "/api/v1/admin/accounts"
UPLOAD = "/api/v1/documents/upload"


async def _promote(client, auth_headers, db_session):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return me


async def _other_headers(register, verify, login, name="Pat Lee"):
    payload, _ = await register(name=name)
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return payload, {"Authorization": f"Bearer {token['access_token']}"}


async def _upload(client, headers, filename="notes.pdf"):
    pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
    uploaded = await client.post(
        UPLOAD,
        headers=headers,
        files=[("files", (filename, pdf, "application/pdf"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()["accepted"][0]


async def _account_id(client, auth_headers, email):
    row = next(
        item
        for item in (await client.get(ACCOUNTS, headers=auth_headers)).json()
        if item["email"] == email
    )
    return row["id"]


class TestAdminDeleteAccount:
    async def test_requires_auth(self, client):
        response = await client.delete(f"{ACCOUNTS}/{uuid.uuid4()}")
        assert response.status_code == 401

    async def test_non_admin_is_forbidden(self, client, auth_headers):
        response = await client.delete(
            f"{ACCOUNTS}/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 403

    async def test_admin_cannot_delete_self(self, client, auth_headers, db_session):
        admin = await _promote(client, auth_headers, db_session)
        response = await client.delete(
            f"{ACCOUNTS}/{admin['id']}", headers=auth_headers
        )
        assert response.status_code == 400
        assert await db_session.get(User, uuid.UUID(admin["id"])) is not None

    async def test_missing_account_is_not_found(
        self, client, auth_headers, db_session
    ):
        await _promote(client, auth_headers, db_session)
        response = await client.delete(
            f"{ACCOUNTS}/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_admin_deletes_another_account_and_its_documents(
        self,
        client,
        auth_headers,
        register,
        verify,
        login,
        fake_storage,
        fake_queue,
        db_session,
        monkeypatch,
    ):
        from app.services import account

        monkeypatch.setattr(account, "get_storage", lambda: fake_storage)
        admin = await _promote(client, auth_headers, db_session)
        other, other_headers = await _other_headers(register, verify, login)
        other_doc = await _upload(client, other_headers, "notes.pdf")
        admin_doc = await _upload(client, auth_headers, "keep.pdf")
        other_id = await _account_id(client, auth_headers, other["email"])

        response = await client.delete(
            f"{ACCOUNTS}/{other_id}", headers=auth_headers
        )
        assert response.status_code == 204, response.text

        assert (
            await db_session.scalar(
                select(User.id).where(User.id == uuid.UUID(other_id))
            )
            is None
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.id == uuid.UUID(other_doc["document_id"]))
            )
            == 0
        )
        assert (
            await db_session.get(Document, uuid.UUID(admin_doc["document_id"]))
            is not None
        )
        assert await db_session.get(User, uuid.UUID(admin["id"])) is not None

        emails = {
            row["email"]
            for row in (await client.get(ACCOUNTS, headers=auth_headers)).json()
        }
        assert other["email"] not in emails
        assert admin["email"] in emails

        login_after = await login(other["email"], other["password"])
        assert login_after.status_code == 401
