"""US-1 (create account), US-2 (login) and NFR-6 (credential exposure)."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from yarrow_db.models import User

from app.api.v1.endpoints.auth import INVALID_CODE, RESEND_REPLY
from app.core.config import settings
from tests.conftest import DEFAULT_PASSWORD, unique_email

REGISTER = "/api/v1/auth/register"
VERIFY = "/api/v1/auth/verify"
RESEND = "/api/v1/auth/resend-verification"
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


class TestVerify:
    """US-1: the emailed code gates the account."""

    async def test_registration_emails_a_six_digit_code(self, register, outbox):
        payload, _ = await register()

        [(to, subject, body)] = outbox.messages
        assert to == payload["email"]
        assert "verification code" in subject.lower()
        assert re.fullmatch(r"\d{6}", outbox.latest_code(to))
        assert payload["password"] not in body

    async def test_correct_code_verifies_account(self, register, verify, login):
        payload, _ = await register()

        response = await verify(payload["email"])

        assert response.status_code == 200, response.text
        assert (await login(payload["email"], payload["password"])).status_code == 200

    async def test_unverified_account_cannot_log_in(self, register, login):
        payload, _ = await register()

        response = await login(payload["email"], payload["password"])

        assert response.status_code == 403
        assert "not verified" in response.json()["detail"]

    async def test_wrong_code_is_rejected(self, register, verify, outbox):
        payload, _ = await register()
        real = outbox.latest_code(payload["email"])
        wrong = "000000" if real != "000000" else "111111"

        response = await verify(payload["email"], wrong)

        assert response.status_code == 400
        assert response.json()["detail"] == INVALID_CODE

    async def test_code_cannot_be_reused(self, register, verify, outbox):
        payload, _ = await register()
        code = outbox.latest_code(payload["email"])
        assert (await verify(payload["email"], code)).status_code == 200

        response = await verify(payload["email"], code)

        assert response.status_code == 400

    async def test_expired_code_is_rejected(self, register, verify, db_session, outbox):
        payload, _ = await register()
        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()
        user.verification_expires_at = datetime.now(UTC).replace(
            tzinfo=None
        ) - timedelta(seconds=1)
        await db_session.commit()

        response = await verify(payload["email"])

        assert response.status_code == 400
        assert response.json()["detail"] == INVALID_CODE

    async def test_malformed_code_is_rejected_by_validation(self, register, verify):
        payload, _ = await register()
        assert (await verify(payload["email"], "12ab")).status_code == 422

    async def test_unknown_email_gets_same_error_as_wrong_code(self, client):
        response = await client.post(
            VERIFY, json={"email": unique_email("ghost"), "code": "123456"}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == INVALID_CODE

    async def test_resend_issues_new_code_and_old_one_stops_working(
        self, register, verify, client, outbox, no_resend_cooldown
    ):
        payload, _ = await register()
        first = outbox.latest_code(payload["email"])

        response = await client.post(RESEND, json={"email": payload["email"]})

        assert response.status_code == 202
        second = outbox.latest_code(payload["email"])
        assert len(outbox.messages) == 2
        assert (await verify(payload["email"], first)).status_code == 400
        assert (await verify(payload["email"], second)).status_code == 200

    async def test_resend_does_not_reveal_whether_email_exists(self, client, outbox):
        response = await client.post(RESEND, json={"email": unique_email("ghost")})

        assert response.status_code == 202
        assert outbox.messages == []

    async def test_seeded_style_verified_user_is_untouched_by_resend(
        self, register, verify, client, outbox
    ):
        payload, _ = await register()
        await verify(payload["email"])
        before = len(outbox.messages)

        await client.post(RESEND, json={"email": payload["email"]})

        assert len(outbox.messages) == before


class TestVerificationLimits:
    """Abuse limits on the unauthenticated verify and resend endpoints."""

    async def test_code_dies_after_max_wrong_attempts(self, register, verify, outbox):
        payload, _ = await register()
        real = outbox.latest_code(payload["email"])
        wrong = "000000" if real != "000000" else "111111"

        for _ in range(settings.VERIFICATION_MAX_ATTEMPTS):
            assert (await verify(payload["email"], wrong)).status_code == 400

        # The budget is spent, so even the right code no longer works.
        response = await verify(payload["email"], real)
        assert response.status_code == 400
        assert response.json()["detail"] == INVALID_CODE

    async def test_new_code_after_lockout_restores_the_budget(
        self, register, verify, client, outbox, no_resend_cooldown
    ):
        payload, _ = await register()
        for _ in range(settings.VERIFICATION_MAX_ATTEMPTS):
            await verify(payload["email"], "999999")

        await client.post(RESEND, json={"email": payload["email"]})

        assert (await verify(payload["email"])).status_code == 200

    async def test_resend_within_cooldown_sends_nothing(self, register, client, outbox):
        payload, _ = await register()

        response = await client.post(RESEND, json={"email": payload["email"]})

        # Same 202 as a real send, so the cooldown leaks nothing.
        assert response.status_code == 202
        assert response.json()["message"] == RESEND_REPLY
        assert len(outbox.messages) == 1

    async def test_hourly_send_cap_includes_registration(
        self, register, client, outbox, no_resend_cooldown
    ):
        payload, _ = await register()

        for _ in range(settings.VERIFICATION_MAX_SENDS_PER_HOUR + 3):
            response = await client.post(RESEND, json={"email": payload["email"]})
            assert response.status_code == 202

        assert len(outbox.messages) == settings.VERIFICATION_MAX_SENDS_PER_HOUR

    async def test_send_cap_resets_after_the_window(
        self, register, verify, client, outbox, db_session, no_resend_cooldown
    ):
        payload, _ = await register()
        for _ in range(settings.VERIFICATION_MAX_SENDS_PER_HOUR):
            await client.post(RESEND, json={"email": payload["email"]})
        capped = len(outbox.messages)

        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()
        user.verification_window_started_at -= timedelta(hours=1, seconds=1)
        await db_session.commit()
        await client.post(RESEND, json={"email": payload["email"]})

        assert len(outbox.messages) == capped + 1
        assert (await verify(payload["email"])).status_code == 200

    async def test_verification_clears_limit_state(self, register, verify, db_session):
        payload, _ = await register()
        await verify(payload["email"], "999999")
        await verify(payload["email"])

        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()
        await db_session.refresh(user)
        assert user.is_verified is True
        assert user.verification_token is None
        assert user.verification_attempts == 0
        assert user.verification_send_count == 0


class TestLogin:
    async def test_correct_credentials_return_bearer_token(
        self, register, verify, login
    ):
        payload, _ = await register()
        await verify(payload["email"])

        response = await login(payload["email"], payload["password"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]

    async def test_wrong_password_is_denied(self, register, verify, login):
        payload, _ = await register()
        await verify(payload["email"])

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
    async def test_returns_current_user(self, register, verify, login, client):
        payload, _ = await register()
        await verify(payload["email"])
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
