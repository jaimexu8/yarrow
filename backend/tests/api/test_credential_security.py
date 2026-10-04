"""NFR-6 Secure Authentication Credentials.

Acceptance criteria (docs/sprint-1-planning.pdf):
  1. Given an account is created, when the password is stored, then the
     plaintext password is not directly stored.
  2. Given credentials are submitted, when authentication occurs, then
     credentials are transmitted through the application's secure
     communication channel.
  3. Given an API response or application log is produced, then sensitive
     authentication credentials are not exposed.

Criterion 2 is ultimately HTTPS, provided by the deployment. What the app
controls is tested here: credentials are only accepted in request bodies (not
URLs, which end up in logs and history), auth responses are never cached, and
with ENFORCE_HTTPS on, plain HTTP is redirected and browsers are told to
always use HTTPS.
"""

import logging
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import User

from app.core.database import get_db
from app.main import app
from tests.conftest import DEFAULT_PASSWORD, FIXTURES_DIR, unique_email

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
HEALTH = "/api/v1/health"
UPLOAD = "/api/v1/documents/upload"
RESET_CONFIRM = "/api/v1/auth/password-reset/confirm"

# Distinctive values, so a leak anywhere is easy to spot.
SECRET_PASSWORD = "Zebra-Quartz-9981"
SECRET_INTERNALS = "postgres://yarrow:db-secret-771@postgres:5432/yarrow"


@pytest.fixture
async def crash_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Like ``client``, but returns 500 responses instead of re-raising the
    exception in the test, so the error body can be inspected."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


class TestPasswordStorage:
    async def test_password_is_stored_only_as_a_bcrypt_hash(self, register, db_session):
        """AC 1."""
        payload, _ = await register(password=SECRET_PASSWORD)

        stored = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()

        assert SECRET_PASSWORD not in stored.hashed_password
        assert stored.hashed_password.startswith("$2b$")


class TestResponsesDoNotLeak:
    """AC 3, responses."""

    async def test_validation_error_does_not_echo_the_password(self, client):
        response = await client.post(
            REGISTER, json={"email": unique_email(), "password": "Zeb-99"}
        )

        assert response.status_code == 422
        assert "Zeb-99" not in response.text

    async def test_validation_error_still_names_the_field(self, client):
        """The frontend maps errors to fields, so loc and msg must remain."""
        response = await client.post(
            REGISTER, json={"email": unique_email(), "password": "short"}
        )

        [issue] = response.json()["detail"]
        assert issue["loc"] == ["body", "password"]
        assert "8 characters" in issue["msg"]

    async def test_reset_validation_error_does_not_echo_the_password(self, client):
        response = await client.post(
            RESET_CONFIRM, json={"token": "x" * 43, "new_password": "Zeb-99"}
        )

        assert response.status_code == 422
        assert "Zeb-99" not in response.text

    async def test_server_error_hides_the_exception_text(
        self, crash_client, monkeypatch
    ):
        from app.api.v1.endpoints import auth

        def explode(_password):
            raise RuntimeError(f"could not connect to {SECRET_INTERNALS}")

        monkeypatch.setattr(auth, "get_password_hash", explode)

        response = await crash_client.post(
            REGISTER, json={"email": unique_email(), "password": SECRET_PASSWORD}
        )

        assert response.status_code == 500
        assert "db-secret-771" not in response.text
        assert SECRET_PASSWORD not in response.text
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"

    async def test_health_check_hides_internal_errors(self, client, monkeypatch):
        """The health check is public, so it must not describe the internals."""
        from app.api.v1.endpoints import health

        def unreachable(_url, **_kwargs):
            raise ConnectionError("redis://:valkey-secret-42@valkey:6379 refused")

        monkeypatch.setattr(health.redis, "from_url", unreachable)

        response = await client.get(HEALTH)

        body = response.json()
        assert body["status"] == "unhealthy"
        assert body["valkey"] == "unavailable"
        assert "valkey-secret-42" not in response.text

    async def test_failed_upload_hides_the_storage_error(
        self, client, auth_headers, fake_storage, fake_queue
    ):
        def broken(_file, _key):
            raise RuntimeError("s3://internal-bucket-7 access key AKIA123 denied")

        fake_storage.upload_file = broken
        pdf = (FIXTURES_DIR / "sample.pdf").read_bytes()

        response = await client.post(
            UPLOAD,
            headers=auth_headers,
            files=[("files", ("sample.pdf", pdf, "application/pdf"))],
        )

        assert response.status_code == 400
        assert "AKIA123" not in response.text
        assert "internal-bucket-7" not in response.text

    async def test_auth_responses_are_never_cached(
        self, client, register, verify, login
    ):
        """RFC 6749 5.1: responses carrying tokens must not be stored."""
        payload, registered = await register()
        await verify(payload["email"])
        signed_in = await login(payload["email"], payload["password"])

        for response in (registered, signed_in):
            assert "no-store" in response.headers["cache-control"]

    async def test_responses_forbid_content_type_sniffing(self, client):
        response = await client.get(HEALTH)
        assert response.headers["x-content-type-options"] == "nosniff"


class TestTransport:
    """AC 2, the parts the application controls."""

    async def test_credentials_in_the_url_are_not_accepted(
        self, client, register, verify
    ):
        """URLs end up in server logs, proxies and browser history, so login
        only reads credentials from the request body."""
        payload, _ = await register()
        await verify(payload["email"])

        response = await client.post(
            LOGIN,
            params={"username": payload["email"], "password": payload["password"]},
        )

        assert response.status_code == 422
        assert "access_token" not in response.text

    async def test_https_is_not_forced_by_default(self, client):
        """Local development runs on plain http://localhost."""
        response = await client.get(HEALTH)

        assert response.status_code == 200
        assert "strict-transport-security" not in response.headers

    async def test_enforce_https_redirects_http_and_sets_hsts(self):
        from app.core.http_security import install_http_security

        demo = FastAPI()

        @demo.get("/ping")
        async def ping():
            return {"ok": True}

        install_http_security(demo, enforce_https=True)
        transport = ASGITransport(app=demo)

        async with AsyncClient(transport=transport, base_url="http://test") as http:
            plain = await http.get("/ping")
        async with AsyncClient(transport=transport, base_url="https://test") as tls:
            secure = await tls.get("/ping")

        assert plain.status_code in (307, 308)
        assert plain.headers["location"].startswith("https://")
        assert secure.status_code == 200
        assert "max-age=" in secure.headers["strict-transport-security"]


class TestLogsDoNotLeak:
    """AC 3, logs."""

    async def test_sign_up_and_sign_in_never_log_the_password(
        self, client, register, verify, login, caplog
    ):
        caplog.set_level(logging.DEBUG)
        email = unique_email()

        await register(email=email, password=SECRET_PASSWORD)
        await verify(email)
        await login(email, "definitely-wrong")
        await login(email, SECRET_PASSWORD)
        await client.post(REGISTER, json={"email": email, "password": "Zeb-99"})

        assert SECRET_PASSWORD not in caplog.text
        assert "Zeb-99" not in caplog.text

    def test_unsent_email_bodies_are_not_logged_by_default(self, caplog):
        """Without a mail server, codes and reset links must not land in the
        logs unless a developer explicitly turns that on."""
        from app.core import mailer

        caplog.set_level(logging.DEBUG)
        mailer.log_unsent_email("a@example.com", "Subject", "Your code is 482915.")

        assert "482915" not in caplog.text
        assert "a@example.com" in caplog.text

    def test_email_bodies_are_logged_when_explicitly_enabled(self, caplog, monkeypatch):
        from app.core import mailer
        from app.core.config import settings

        monkeypatch.setattr(settings, "LOG_EMAIL_BODIES", True)
        caplog.set_level(logging.DEBUG)
        mailer.log_unsent_email("a@example.com", "Subject", "Your code is 482915.")

        assert "482915" in caplog.text


def test_default_password_constant_is_not_used_as_a_secret():
    """Guard for this file: the leak checks use their own distinctive values."""
    assert SECRET_PASSWORD != DEFAULT_PASSWORD
