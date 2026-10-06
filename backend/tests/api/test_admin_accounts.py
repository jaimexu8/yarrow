"""us-70: admin list of all accounts."""

import uuid

from yarrow_db.models import User

from tests.conftest import FIXTURES_DIR

ACCOUNTS = "/api/v1/admin/accounts"
UPLOAD = "/api/v1/documents/upload"


async def _promote(client, auth_headers, db_session):
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.is_admin = True
    await db_session.flush()
    return me


async def _other_headers(register, verify, login, name="Other User"):
    payload, _ = await register(name=name)
    await verify(payload["email"])
    token = (await login(payload["email"], payload["password"])).json()
    return payload, {"Authorization": f"Bearer {token['access_token']}"}


class TestAdminAccounts:
    async def test_requires_auth(self, client):
        assert (await client.get(ACCOUNTS)).status_code == 401

    async def test_non_admin_is_forbidden(self, client, auth_headers):
        response = await client.get(ACCOUNTS, headers=auth_headers)
        assert response.status_code == 403

    async def test_admin_sees_all_accounts(
        self, client, auth_headers, register, verify, login, db_session
    ):
        admin = await _promote(client, auth_headers, db_session)
        other, _ = await _other_headers(register, verify, login)

        response = await client.get(ACCOUNTS, headers=auth_headers)
        assert response.status_code == 200
        emails = {row["email"] for row in response.json()}
        assert admin["email"] in emails
        assert other["email"] in emails

    async def test_account_shows_name_email_join_date_and_doc_count(
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
        await _promote(client, auth_headers, db_session)
        other, other_headers = await _other_headers(
            register, verify, login, name="Pat Lee"
        )
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()
        uploaded = await client.post(
            UPLOAD,
            headers=other_headers,
            files=[("files", ("notes.pdf", pdf, "application/pdf"))],
        )
        assert uploaded.status_code == 200, uploaded.text

        response = await client.get(ACCOUNTS, headers=auth_headers)
        assert response.status_code == 200
        row = next(item for item in response.json() if item["email"] == other["email"])
        assert row["name"] == "Pat Lee"
        assert row["created_at"]
        assert row["document_count"] == 1
        assert row["is_admin"] is False
        assert "hashed_password" not in row
        assert "password" not in row

    async def test_new_account_has_zero_documents(
        self, client, auth_headers, db_session
    ):
        admin = await _promote(client, auth_headers, db_session)
        response = await client.get(ACCOUNTS, headers=auth_headers)
        row = next(item for item in response.json() if item["email"] == admin["email"])
        assert row["document_count"] == 0
        assert row["is_admin"] is True
