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

    async def test_admin_can_disable_and_enable_an_account(
        self, client, auth_headers, register, verify, login, db_session
    ):
        # Promote the current user to admin
        await _promote(client, auth_headers, db_session)

        other, _ = await _other_headers(register, verify, login)

        # Get the account info of the other account
        account = next(
            row
            for row in (await client.get(ACCOUNTS, headers=auth_headers)).json()
            if row["email"] == other["email"]
        )

        # Verify that disabling the account works correctly
        response = await client.patch(
            f"{ACCOUNTS}/{account['id']}/status",
            headers=auth_headers,
            json={"is_active": False},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

        # Verify that enabling the account works correctly
        response = await client.patch(
            f"{ACCOUNTS}/{account['id']}/status",
            headers=auth_headers,
            json={"is_active": True},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    async def test_admin_cannot_disable_self(self, client, auth_headers, db_session):
        # Promote the current user to admin
        admin = await _promote(client, auth_headers, db_session)

        # Verify that disabling the admin's own account is not allowed
        response = await client.patch(
            f"{ACCOUNTS}/{admin['id']}/status",
            headers=auth_headers,
            json={"is_active": False},
        )
        assert response.status_code == 400

    async def test_admin_can_promote_an_account(
        self, client, auth_headers, register, verify, login, db_session
    ):
        await _promote(client, auth_headers, db_session)
        other, _ = await _other_headers(register, verify, login)
        account = next(
            row
            for row in (await client.get(ACCOUNTS, headers=auth_headers)).json()
            if row["email"] == other["email"]
        )

        response = await client.patch(
            f"{ACCOUNTS}/{account['id']}/status",
            headers=auth_headers,
            json={"is_admin": True},
        )

        assert response.status_code == 200
        assert response.json()["is_admin"] is True
