"""US-67 Reset Password.

Acceptance criteria (docs/sprint-1-planning.pdf):
  1. Given I have a registered account, when I request a password reset, then
     reset instructions are sent to my email.
  2. Given I have a valid reset request, when I submit a valid new password,
     then my password is updated.
  3. Given the reset request is invalid or expired, when I attempt to reset my
     password, then the request is rejected.
  4. Given my password was successfully reset, when I log in with the new
     password, then authentication succeeds.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from yarrow_db.models import User

from app.core.config import settings
from tests.conftest import DEFAULT_PASSWORD, RESET_LINK_PATTERN, unique_email

REQUEST = "/api/v1/auth/password-reset/request"
CONFIRM = "/api/v1/auth/password-reset/confirm"
NEW_PASSWORD = "a-brand-new-passphrase"


async def _verified_user(register, verify) -> dict:
    payload, _ = await register()
    await verify(payload["email"])
    return payload


async def _load_user(db_session, email: str) -> User:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    await db_session.refresh(user)
    return user


class TestRequestReset:
    async def test_registered_email_gets_a_reset_link(
        self, client, register, verify, outbox
    ):
        """AC 1."""
        user = await _verified_user(register, verify)

        response = await client.post(REQUEST, json={"email": user["email"]})

        assert response.status_code == 202
        [body] = outbox.reset_emails(user["email"])
        link = RESET_LINK_PATTERN.search(body)
        assert link.group(1) == f"{settings.NEXT_PUBLIC_APP_URL}/reset-password"
        assert len(link.group(2)) >= 40  # 256 random bits, url-safe base64

    async def test_unknown_email_gets_the_same_reply_and_no_email(
        self, client, register, verify, outbox
    ):
        """The reply must not reveal which addresses have accounts."""
        user = await _verified_user(register, verify)
        known = await client.post(REQUEST, json={"email": user["email"]})

        ghost = unique_email("ghost")
        unknown = await client.post(REQUEST, json={"email": ghost})

        assert unknown.status_code == known.status_code == 202
        assert unknown.json() == known.json()
        assert outbox.reset_emails(ghost) == []

    async def test_email_capitalization_does_not_matter(
        self, client, register, verify, outbox
    ):
        user = await _verified_user(register, verify)

        await client.post(REQUEST, json={"email": user["email"].upper()})

        assert len(outbox.reset_emails(user["email"])) == 1

    async def test_only_a_hash_of_the_token_is_stored(
        self, client, register, verify, outbox, db_session
    ):
        """A database leak must not hand out working reset links."""
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])

        stored = await _load_user(db_session, user["email"])

        assert stored.reset_token
        assert token not in stored.reset_token

    async def test_second_request_within_cooldown_sends_nothing(
        self, client, register, verify, outbox
    ):
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})

        response = await client.post(REQUEST, json={"email": user["email"]})

        assert response.status_code == 202
        assert len(outbox.reset_emails(user["email"])) == 1

    async def test_hourly_cap_limits_emails(
        self, client, register, verify, outbox, no_reset_cooldown
    ):
        user = await _verified_user(register, verify)

        for _ in range(settings.PASSWORD_RESET_MAX_PER_HOUR + 3):
            response = await client.post(REQUEST, json={"email": user["email"]})
            assert response.status_code == 202

        sent = len(outbox.reset_emails(user["email"]))
        assert sent == settings.PASSWORD_RESET_MAX_PER_HOUR


class TestConfirmReset:
    async def test_valid_link_updates_the_password(
        self, client, register, verify, outbox, login
    ):
        """AC 2 and AC 4: the new password works, the old one does not."""
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])

        response = await client.post(
            CONFIRM, json={"token": token, "new_password": NEW_PASSWORD}
        )

        assert response.status_code == 200, response.text
        assert NEW_PASSWORD not in response.text
        assert (await login(user["email"], NEW_PASSWORD)).status_code == 200
        assert (await login(user["email"], DEFAULT_PASSWORD)).status_code == 401

    async def test_link_works_only_once(self, client, register, verify, outbox):
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])
        body = {"token": token, "new_password": NEW_PASSWORD}
        assert (await client.post(CONFIRM, json=body)).status_code == 200

        again = await client.post(CONFIRM, json=body)

        assert again.status_code == 400

    async def test_expired_link_is_rejected(
        self, client, register, verify, outbox, db_session, login
    ):
        """AC 3: expired."""
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])
        stored = await _load_user(db_session, user["email"])
        stored.reset_token_expires_at = datetime.now(UTC).replace(
            tzinfo=None
        ) - timedelta(seconds=1)
        await db_session.commit()

        response = await client.post(
            CONFIRM, json={"token": token, "new_password": NEW_PASSWORD}
        )

        assert response.status_code == 400
        assert (await login(user["email"], DEFAULT_PASSWORD)).status_code == 200

    async def test_made_up_token_is_rejected(self, client):
        """AC 3: invalid."""
        response = await client.post(
            CONFIRM,
            json={"token": "x" * 43, "new_password": NEW_PASSWORD},
        )

        assert response.status_code == 400

    async def test_newer_request_cancels_the_older_link(
        self, client, register, verify, outbox, no_reset_cooldown
    ):
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        first = outbox.latest_reset_token(user["email"])
        await client.post(REQUEST, json={"email": user["email"]})
        second = outbox.latest_reset_token(user["email"])

        old = await client.post(
            CONFIRM, json={"token": first, "new_password": NEW_PASSWORD}
        )
        new = await client.post(
            CONFIRM, json={"token": second, "new_password": NEW_PASSWORD}
        )

        assert old.status_code == 400
        assert new.status_code == 200

    async def test_weak_password_is_refused_and_link_still_works(
        self, client, register, verify, outbox
    ):
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])

        weak = await client.post(
            CONFIRM, json={"token": token, "new_password": "short"}
        )
        ok = await client.post(
            CONFIRM, json={"token": token, "new_password": NEW_PASSWORD}
        )

        assert weak.status_code == 422
        assert ok.status_code == 200

    async def test_reset_also_verifies_an_unverified_account(
        self, client, register, outbox, login
    ):
        """Opening the emailed link proves the user owns the address."""
        payload, _ = await register()
        await client.post(REQUEST, json={"email": payload["email"]})
        token = outbox.latest_reset_token(payload["email"])

        await client.post(CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

        assert (await login(payload["email"], NEW_PASSWORD)).status_code == 200


class TestFailClosed:
    async def test_rate_limit_store_outage_gives_generic_503(
        self, client, register, verify, outbox, monkeypatch
    ):
        """Without Valkey the limits can't be enforced, so no email is sent,
        and no infrastructure detail reaches the client."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.api.v1.endpoints import auth

        user = await _verified_user(register, verify)

        async def unreachable(_email):
            raise RedisConnectionError("valkey:6379 is down")

        monkeypatch.setattr(auth, "reserve_reset_email", unreachable)

        response = await client.post(REQUEST, json={"email": user["email"]})

        assert response.status_code == 503
        assert "valkey" not in response.text.lower()
        assert outbox.reset_emails(user["email"]) == []


class TestSessionsAfterReset:
    async def test_reset_signs_out_existing_sessions(
        self, client, register, verify, login, outbox
    ):
        """If the reset follows a compromise, the attacker's token must stop
        working too, not just the old password."""
        user = await _verified_user(register, verify)
        old = (await login(user["email"], DEFAULT_PASSWORD)).json()["access_token"]
        headers = {"Authorization": f"Bearer {old}"}
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])

        await client.post(CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401

    async def test_signing_in_right_after_reset_works(
        self, client, register, verify, login, outbox
    ):
        """A new session in the same second as the reset is not refused."""
        user = await _verified_user(register, verify)
        await client.post(REQUEST, json={"email": user["email"]})
        token = outbox.latest_reset_token(user["email"])
        await client.post(CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

        fresh = (await login(user["email"], NEW_PASSWORD)).json()["access_token"]
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh}"}
        )

        assert me.status_code == 200


class TestHourlyCounter:
    async def test_counter_without_expiry_gets_one(self):
        """A counter left with no expiry would block an address forever once
        capped. The next reservation must give it one."""
        from app.core.password_reset import ONE_HOUR_SECONDS, reserve_reset_email
        from app.core.valkey import get_valkey

        email = unique_email("stuck")
        valkey = get_valkey()
        await valkey.set(f"pwreset:hour:{email}", 99)  # no expiry

        allowed = await reserve_reset_email(email)

        ttl = await valkey.ttl(f"pwreset:hour:{email}")
        assert allowed is False
        assert 0 < ttl <= ONE_HOUR_SECONDS
