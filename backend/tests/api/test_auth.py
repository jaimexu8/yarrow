"""US-1 (create account), US-2 (login) and NFR-6 (credential exposure)."""

from tests.conftest import DEFAULT_PASSWORD, unique_email

REGISTER = "/api/v1/auth/register"
ME = "/api/v1/auth/me"


class TestRegister:
    async def test_valid_registration_creates_account(self, register):
        payload, response = await register()

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] == payload["email"]
        assert body["name"] == payload["name"]
        assert body["is_admin"] is False
        assert "id" in body

    async def test_response_never_exposes_credentials(self, register):
        _, response = await register()

        body = response.json()
        assert "password" not in body
        assert "hashed_password" not in body
        assert DEFAULT_PASSWORD not in response.text

    async def test_duplicate_email_is_rejected(self, register):
        payload, first = await register()
        assert first.status_code == 201

        _, second = await register(email=payload["email"])

        assert second.status_code == 409
        assert "already exists" in second.json()["detail"]

    async def test_invalid_email_is_rejected(self, register):
        _, response = await register(email="not-an-email")
        assert response.status_code == 422

    async def test_short_password_is_rejected(self, register):
        _, response = await register(password="short")
        assert response.status_code == 422

    async def test_missing_password_is_rejected(self, client):
        response = await client.post(REGISTER, json={"email": unique_email()})
        assert response.status_code == 422


class TestLogin:
    async def test_correct_credentials_return_bearer_token(self, register, login):
        payload, _ = await register()

        response = await login(payload["email"], payload["password"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]

    async def test_wrong_password_is_denied(self, register, login):
        payload, _ = await register()

        response = await login(payload["email"], "definitely-wrong")

        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    async def test_unknown_email_is_denied_with_same_message(self, register, login):
        """Unknown email and wrong password must be indistinguishable so login
        cannot be used to enumerate registered addresses (NFR-6)."""
        payload, _ = await register()
        wrong_password = await login(payload["email"], "definitely-wrong")
        unknown_email = await login(unique_email("nobody"), payload["password"])

        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


class TestMe:
    async def test_returns_current_user(self, register, login, client):
        payload, _ = await register()
        token = (await login(payload["email"], payload["password"])).json()

        response = await client.get(
            ME, headers={"Authorization": f"Bearer {token['access_token']}"}
        )

        assert response.status_code == 200
        assert response.json()["email"] == payload["email"]
        assert "hashed_password" not in response.json()

    async def test_requires_token(self, client):
        response = await client.get(ME)
        assert response.status_code == 401

    async def test_rejects_garbage_token(self, client):
        response = await client.get(ME, headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401
