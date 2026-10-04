"""Shared async client for Valkey (Redis-compatible), used by the auth code.

Holds the token denylist (US-19) and the password reset rate limits (US-67).
"""

import asyncio
from weakref import WeakKeyDictionary

import redis.asyncio as redis

from .config import settings

# One client per event loop. A redis.asyncio client is bound to the loop it
# first ran on; a single global client would break wherever a new loop is
# started, such as between tests. In the running server there is one loop, so
# this is effectively one shared client.
_clients: "WeakKeyDictionary[asyncio.AbstractEventLoop, redis.Redis]" = (
    WeakKeyDictionary()
)


def get_valkey() -> redis.Redis:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = redis.from_url(
            f"redis://{settings.VALKEY_HOST}:{settings.VALKEY_PORT}/0",
            decode_responses=True,
            # Signed-in requests wait on the denylist lookup. Without limits,
            # a Valkey that accepts connections but stops answering would hang
            # the whole app; with them, callers fail fast into a clean 503.
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        _clients[loop] = client
    return client
