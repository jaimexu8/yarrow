"""Row-lock timeouts shared by the backend and the worker.

A request that locks a document row should fail fast when the worker is holding
it, rather than hang on the user; these are the pieces every such caller needs.
"""

import re

from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError

# The time to wait for a row lock before failing fast.
LOCK_TIMEOUT = "5s"

# Postgres lock_not_available, raised when a lock_timeout expires.
LOCK_NOT_AVAILABLE = "55P03"

_TIMEOUT_PATTERN = re.compile(r"\d+(ms|s|min)?")


def lock_timeout_statement(timeout: str = LOCK_TIMEOUT) -> TextClause:
    """SET LOCAL lock_timeout for the current transaction"""
    if not _TIMEOUT_PATTERN.fullmatch(timeout):
        raise ValueError(f"Invalid lock timeout: {timeout!r}")
    return text(f"SET LOCAL lock_timeout = '{timeout}'")


def is_lock_timeout(exc: DBAPIError) -> bool:
    return (
        getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE
        or getattr(exc.orig, "pgcode", None) == LOCK_NOT_AVAILABLE
    )
