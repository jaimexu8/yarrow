from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db import Base, Document, User, get_async_engine, get_engine

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app


@pytest.fixture(scope="session")
def create_tables():
    # make tables if this db is empty
    Base.metadata.create_all(get_engine())


@pytest.fixture
async def db_session(create_tables):
    engine = get_async_engine()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            # drop test rows when the test ends
            await transaction.rollback()


@pytest.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def user(db_session):
    row = User(
        email=f"user-{uuid4()}@test.local",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
        storage_used_bytes=0,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def other_user(db_session):
    row = User(
        email=f"other-{uuid4()}@test.local",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
        storage_used_bytes=0,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
def auth_headers(user):
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def add_document(db_session):
    async def _add(owner_id, filename):
        doc = Document(
            owner_id=owner_id,
            filename=filename,
            file_size_bytes=100,
            file_type="application/pdf",
            storage_key=f"documents/{uuid4()}/original",
            status="completed",
        )
        db_session.add(doc)
        await db_session.flush()
        return doc

    return _add
