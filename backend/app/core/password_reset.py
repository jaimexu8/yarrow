"""Password reset links (US-67).

The emailed link carries a random 256-bit token. Guessing one is not
feasible, so unlike the 6-digit verification code it needs no attempt limit.
Only an HMAC of the token is stored, so a database leak does not hand out
working links. A token works once, expires after PASSWORD_RESET_TTL_MINUTES,
and requesting a new link replaces the old one.

How often a reset email can be sent to one address is limited in Valkey (a
cooldown plus an hourly cap), which keeps the endpoint from being used to
flood someone's inbox.
"""

import hmac
import secrets
from datetime import timedelta
from hashlib import sha256

from yarrow_db.models import User

from .config import settings
from .valkey import get_valkey
from .verification import utcnow

ONE_HOUR_SECONDS = 3600

# Count one send and make sure the counter expires, in a single atomic step.
# Done as separate commands, the key could expire between them and be
# recreated with no expiry, blocking the address forever once capped.
_COUNT_SEND = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def hash_reset_token(token: str) -> str:
    # The prefix keeps these hashes distinct from verification-code hashes,
    # which use the same secret key.
    message = f"password-reset:{token}".encode()
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, sha256).hexdigest()


def issue_reset_token(user: User) -> str:
    """Give the user a fresh reset token (not committed); return the plaintext.

    Overwrites any earlier token, so only the newest emailed link works.
    """
    token = secrets.token_urlsafe(32)
    user.reset_token = hash_reset_token(token)
    user.reset_token_expires_at = utcnow() + timedelta(
        minutes=settings.PASSWORD_RESET_TTL_MINUTES
    )
    return token


def reset_token_is_live(user: User) -> bool:
    return (
        user.reset_token_expires_at is not None
        and user.reset_token_expires_at > utcnow()
    )


async def reserve_reset_email(email: str) -> bool:
    """Record one reset email for this address, if the limits allow it.

    Called for every request, whether or not the address has an account, so
    the endpoint behaves the same either way. Raises RedisError if Valkey is
    unavailable; the caller then refuses the request rather than sending mail
    without limits.
    """
    valkey = get_valkey()

    cooldown = settings.PASSWORD_RESET_COOLDOWN_SECONDS
    if cooldown > 0:
        # SET NX: succeeds only if no reset email went out in the cooldown.
        fresh = await valkey.set(f"pwreset:cooldown:{email}", "1", ex=cooldown, nx=True)
        if not fresh:
            return False

    sent_this_hour = await valkey.eval(
        _COUNT_SEND, 1, f"pwreset:hour:{email}", ONE_HOUR_SECONDS
    )
    return int(sent_this_hour) <= settings.PASSWORD_RESET_MAX_PER_HOUR
