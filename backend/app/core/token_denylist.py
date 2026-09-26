"""Revoked access tokens (US-19 Secure Logout).

Access tokens are stateless JWTs: the server keeps no record of them and only
checks their signature and expiry. So deleting a token in the browser does not
end anything, and a copied token would keep working until it expired. Logout
therefore records the token's unique id (its ``jti``) here, and every
authenticated request checks this list.

Each entry expires when the token itself would have. After that moment the
token is rejected for being expired anyway, so the entry has no purpose, and
Valkey removing it automatically keeps the list from growing forever.
"""

import math
from datetime import UTC, datetime

from .valkey import get_valkey

KEY_PREFIX = "revoked_token:"

# How long an entry outlives the token's own expiry. The JWT library still
# accepts a token during the second it expires, and server clocks drift a
# little, so an entry that vanished exactly at "exp" could let a revoked token
# work again for a moment. Thirty seconds covers both with room to spare.
REVOCATION_GRACE_SECONDS = 30


def _key(jti: str) -> str:
    return f"{KEY_PREFIX}{jti}"


async def revoke_token(jti: str, expires_at: datetime) -> None:
    """Mark a token as revoked until shortly after it would have expired."""
    ttl = (expires_at - datetime.now(UTC)).total_seconds() + REVOCATION_GRACE_SECONDS
    if ttl <= 0:
        # Long expired: the JWT check rejects it without any entry.
        return
    # Rounded up so the entry never disappears early.
    await get_valkey().set(_key(jti), "1", ex=math.ceil(ttl))


async def is_token_revoked(jti: str) -> bool:
    return await get_valkey().exists(_key(jti)) == 1
