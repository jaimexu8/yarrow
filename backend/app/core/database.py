"""Thin re-export of the shared database layer.

The engines, session factories and models live in the ``yarrow-db`` package so
that the Celery worker can use exactly the same definitions. This module only
keeps the names the FastAPI app already depends on.
"""

from yarrow_db.session import (
    get_async_engine as _get_async_engine,
)
from yarrow_db.session import (
    get_async_session as get_db,
)
from yarrow_db.session import (
    get_async_session_maker,
)

__all__ = ["get_async_session_maker", "get_db"]

engine = _get_async_engine()
async_session_maker = get_async_session_maker()
