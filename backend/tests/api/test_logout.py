"""US-19 Secure Logout.

These tests are the spec for the feature. They fail until the code exists.

Acceptance criteria (docs/sprint-1-planning.pdf):
  1. Given I am logged in, when I select logout, then my authenticated
     session is ended.
  2. Given I have logged out, when I attempt to access a protected page,
     then I am required to log in again.
  3. Given logout succeeds, when the application updates, then my
     authenticated user information is no longer available.

Criterion 2 is also enforced by the frontend route guard; here we prove the
server side: an old token is refused, so nothing protected can be reached.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.security import decode_access_token

LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


async def _sign_in(register, verify, login) -> str:
    """A fresh verified user; returns their access token."""
    payload, _ = await register()
    await verify(payload["email"])
    response = await login(payload["email"], payload["password"])
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTokenId:
    """Step 1: every token needs its own id so it can be revoked alone."""

    async def test_token_carries_a_jti(self, register, verify, login):
        token = await _sign_in(register, verify, login)

        payload = decode_access_token(token)

        assert payload is not None
        assert isinstance(payload.get("jti"), str) and payload["jti"]

    async def test_each_sign_in_gets_a_different_jti(self, register, verify, login):
        payload, _ = await register()
        await verify(payload["email"])
        first = await login(payload["email"], payload["password"])
        second = await login(payload["email"], payload["password"])

        jti_1 = decode_access_token(first.json()["access_token"])["jti"]
        jti_2 = decode_access_token(second.json()["access_token"])["jti"]

        assert jti_1 != jti_2


class TestDenylist:
    """Step 2: the Valkey-backed denylist, tested on its own."""

    async def test_unknown_token_is_not_revoked(self):
        from app.core.token_denylist import is_token_revoked

        assert await is_token_revoked("never-issued-jti") is False

    async def test_revoked_token_is_reported(self):
        from app.core.token_denylist import is_token_revoked, revoke_token

        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await revoke_token("jti-to-revoke", expires_at)

        assert await is_token_revoked("jti-to-revoke") is True

    async def test_entry_disappears_after_the_token_expires(self, monkeypatch):
        """Revoked ids must not pile up forever. Grace is zeroed here so the
        test doesn't have to wait out the real 30 seconds."""
        from app.core import token_denylist
        from app.core.token_denylist import is_token_revoked, revoke_token

        monkeypatch.setattr(token_denylist, "REVOCATION_GRACE_SECONDS", 0)
        expires_at = datetime.now(UTC) + timedelta(seconds=1)
        await revoke_token("short-lived-jti", expires_at)
        await asyncio.sleep(1.5)

        assert await is_token_revoked("short-lived-jti") is False

    async def test_entry_outlives_the_token_by_the_grace_period(self):
        """The JWT check still accepts a token during its final second, so
        the entry must still exist then."""
        from app.core.token_denylist import (
            REVOCATION_GRACE_SECONDS,
            _client,
            _key,
            revoke_token,
        )

        expires_at = datetime.now(UTC) + timedelta(seconds=10)
        await revoke_token("boundary-jti", expires_at)

        ttl = await _client().ttl(_key("boundary-jti"))
        assert (
            10 + REVOCATION_GRACE_SECONDS - 2
            <= ttl
            <= 10 + REVOCATION_GRACE_SECONDS + 1
        )

    async def test_already_expired_token_needs_no_entry(self):
        """Revoking a token that has already expired must not raise."""
        from app.core.token_denylist import revoke_token

        await revoke_token("old-jti", datetime.now(UTC) - timedelta(minutes=1))


class TestLogout:
    """Steps 3 and 4: the endpoint, and the check on every request."""

    async def test_logout_succeeds_with_no_content(
        self, client, register, verify, login
    ):
        token = await _sign_in(register, verify, login)

        response = await client.post(LOGOUT, headers=_bearer(token))

        assert response.status_code == 204

    async def test_token_is_refused_after_logout(self, client, register, verify, login):
        """AC 1 and 3: the session is over and /me no longer answers."""
        token = await _sign_in(register, verify, login)
        assert (await client.get(ME, headers=_bearer(token))).status_code == 200

        await client.post(LOGOUT, headers=_bearer(token))

        response = await client.get(ME, headers=_bearer(token))
        assert response.status_code == 401

    async def test_logout_twice_is_refused_the_second_time(
        self, client, register, verify, login
    ):
        token = await _sign_in(register, verify, login)
        await client.post(LOGOUT, headers=_bearer(token))

        response = await client.post(LOGOUT, headers=_bearer(token))

        assert response.status_code == 401

    async def test_logout_requires_a_token(self, client):
        assert (await client.post(LOGOUT)).status_code == 401

    async def test_other_sessions_stay_signed_in(self, client, register, verify, login):
        """Logging out on one device does not sign you out everywhere."""
        payload, _ = await register()
        await verify(payload["email"])
        laptop = (await login(payload["email"], payload["password"])).json()
        phone = (await login(payload["email"], payload["password"])).json()

        await client.post(LOGOUT, headers=_bearer(laptop["access_token"]))

        still_in = await client.get(ME, headers=_bearer(phone["access_token"]))
        assert still_in.status_code == 200


class TestFailClosed:
    async def test_denylist_outage_refuses_requests(
        self, client, register, verify, login, monkeypatch
    ):
        """If Valkey is down, a revoked token can't be told from a live one,
        so requests are refused with 503 rather than waved through."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.core import security

        token = await _sign_in(register, verify, login)

        async def unreachable(_jti):
            raise RedisConnectionError("valkey is down")

        monkeypatch.setattr(security, "is_token_revoked", unreachable)

        response = await client.get(ME, headers=_bearer(token))

        assert response.status_code == 503
        assert "valkey" not in response.text.lower()

    async def test_token_without_jti_is_refused(self, client, register, verify):
        """Tokens issued before US-19 have no jti and can't be revoked."""
        from jose import jwt

        from app.core.config import settings

        payload, response = await register()
        await verify(payload["email"])
        legacy = jwt.encode(
            {
                "sub": response.json()["id"],
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        assert (await client.get(ME, headers=_bearer(legacy))).status_code == 401

    async def test_failed_revocation_is_not_reported_as_logged_out(
        self, client, register, verify, login, monkeypatch
    ):
        """If the revocation can't be saved, the token still works, so the
        endpoint must not answer 204. And no internal detail may leak."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.api.v1.endpoints import auth

        token = await _sign_in(register, verify, login)

        async def write_fails(_jti, _expires_at):
            raise RedisConnectionError("valkey:6379 refused the write")

        monkeypatch.setattr(auth, "revoke_token", write_fails)

        response = await client.post(LOGOUT, headers=_bearer(token))

        assert response.status_code == 503
        assert "valkey" not in response.text.lower()
        assert "6379" not in response.text

    async def test_token_expiring_during_logout_counts_as_logged_out(
        self, client, register, verify, login, monkeypatch
    ):
        """A token that expires between the auth check and the endpoint body
        is already unusable: logout succeeds instead of crashing."""
        from app.api.v1.endpoints import auth

        token = await _sign_in(register, verify, login)
        monkeypatch.setattr(auth, "decode_access_token", lambda _token: None)

        response = await client.post(LOGOUT, headers=_bearer(token))

        assert response.status_code == 204
