"""Email verification codes (US-1).

A six-digit code is emailed on registration and stored only as an HMAC of the
code, so a database read does not reveal a usable code. Codes expire after
``VERIFICATION_CODE_TTL_MINUTES`` and are cleared once used.

Abuse limits, because the verify and resend endpoints are unauthenticated:

- Each code allows ``VERIFICATION_MAX_ATTEMPTS`` wrong guesses.
- A new code can be sent at most once per ``VERIFICATION_RESEND_COOLDOWN_SECONDS``
  and ``VERIFICATION_MAX_SENDS_PER_HOUR`` times per hour, registration included.
  That caps both inbox flooding and total guesses (sends x attempts per hour).

Both endpoints load the user with ``lock_user_by_email``, a ``SELECT ... FOR
UPDATE``, so verify and resend on one account run strictly one at a time. That
is what makes the counters hold under concurrent requests, and what stops a
resend landing mid-verify from letting a replaced code through.
"""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import User

from .config import settings

SEND_WINDOW = timedelta(hours=1)


def utcnow() -> datetime:
    """Naive UTC, matching the DateTime columns (which have no timezone)."""
    return datetime.now(UTC).replace(tzinfo=None)


def generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), code.encode("utf-8"), sha256
    ).hexdigest()


def can_send_code(user: User) -> bool:
    """False while in the resend cooldown or over the hourly send cap."""
    now = utcnow()
    if user.verification_sent_at is not None and now - user.verification_sent_at < (
        timedelta(seconds=settings.VERIFICATION_RESEND_COOLDOWN_SECONDS)
    ):
        return False
    window_open = (
        user.verification_window_started_at is not None
        and now - user.verification_window_started_at < SEND_WINDOW
    )
    sent = user.verification_send_count or 0
    return not (window_open and sent >= settings.VERIFICATION_MAX_SENDS_PER_HOUR)


def issue_verification_code(user: User) -> str:
    """Set a fresh code on the user (not committed) and return the plaintext.

    Callers check ``can_send_code`` first; this records the send against the
    hourly window and resets the attempt budget for the new code.
    """
    now = utcnow()
    code = generate_code()
    user.verification_token = hash_code(code)
    user.verification_expires_at = now + timedelta(
        minutes=settings.VERIFICATION_CODE_TTL_MINUTES
    )
    user.verification_attempts = 0
    user.verification_sent_at = now
    window = user.verification_window_started_at
    if window is None or now - window >= SEND_WINDOW:
        user.verification_window_started_at = now
        user.verification_send_count = 1
    else:
        user.verification_send_count = (user.verification_send_count or 0) + 1
    return code


async def lock_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Load the user with a row lock held until the session commits or rolls back.

    ``populate_existing`` re-reads the row even if the session already has the
    object cached, so the caller always sees the state it has locked.
    """
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


def attempts_exhausted(user: User) -> bool:
    return (user.verification_attempts or 0) >= settings.VERIFICATION_MAX_ATTEMPTS


def code_matches(user: User, code: str) -> bool:
    """True only for a stored, unexpired code equal to ``code``."""
    if not user.verification_token or user.verification_expires_at is None:
        return False
    if user.verification_expires_at < utcnow():
        return False
    return hmac.compare_digest(user.verification_token, hash_code(code))


def clear_verification_state(user: User) -> None:
    user.verification_token = None
    user.verification_expires_at = None
    user.verification_attempts = 0
    user.verification_sent_at = None
    user.verification_send_count = 0
    user.verification_window_started_at = None
