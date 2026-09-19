"""Engines and session factories for both async (FastAPI) and sync (Celery) callers.

Engines are created lazily so that importing a model never opens a connection
pool -- Alembic and unit tests import :mod:`yarrow_db.models` without needing a
reachable database.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import db_settings


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    return create_async_engine(db_settings.async_url, echo=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(db_settings.sync_url, echo=False, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_async_session_maker() -> sessionmaker:
    return sessionmaker(
        get_async_engine(), class_=AsyncSession, expire_on_commit=False
    )


@lru_cache(maxsize=1)
def get_session_maker() -> sessionmaker:
    return sessionmaker(get_engine(), class_=Session, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency."""
    async with get_async_session_maker()() as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Sync context manager for Celery tasks: commits on success, rolls back on error."""
    session = get_session_maker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
