"""Shared fixtures for the backend API tests.

The tests run against a real Postgres -- the compose stack locally, the service
container in CI -- but in a separate ``yarrow_test`` database, so they never
touch development data. The schema is built by the Alembic migrations rather
than ``metadata.create_all`` so that a migration that drifts from the models
fails here instead of on deploy. Every test runs inside one transaction that is
rolled back at the end, so tests cannot see each other's rows.

Local run (with ``docker compose up`` in another terminal)::

    cd backend && poetry run pytest
"""

import asyncio
import os
import re
import uuid
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment. This block must run before anything imports ``yarrow_db`` or
# ``app``: their settings objects are module-level singletons that read the
# environment exactly once.
# ---------------------------------------------------------------------------
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("VALKEY_HOST", "localhost")
os.environ["POSTGRES_DB"] = os.environ.get("POSTGRES_TEST_DB", "yarrow_test")

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from yarrow_db.config import db_settings

from alembic import command
from app.core.database import get_db
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Database: created once per session, migrated to head.
# ---------------------------------------------------------------------------
async def _ensure_database_exists() -> None:
    """Create the test database if it is missing.

    Connects to the ``postgres`` maintenance database because you cannot
    create a database from inside itself, and CREATE DATABASE cannot run in
    a transaction, so plain asyncpg is used rather than SQLAlchemy.
    """
    conn = await asyncpg.connect(
        user=db_settings.POSTGRES_USER,
        password=db_settings.POSTGRES_PASSWORD,
        host=db_settings.POSTGRES_HOST,
        port=int(db_settings.POSTGRES_PORT),
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_settings.POSTGRES_DB
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_settings.POSTGRES_DB}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Sync on purpose: alembic's env.py calls ``asyncio.run`` itself, which
    would fail inside an already-running event loop."""
    asyncio.run(_ensure_database_exists())
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# Per-test session and client.
# ---------------------------------------------------------------------------
@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session whose work is rolled back when the test ends.

    The session joins an outer connection-level transaction in
    ``create_savepoint`` mode, so the ``await db.commit()`` calls inside the
    endpoints release a savepoint instead of committing for real.
    """
    engine = create_async_engine(db_settings.async_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """httpx client talking to the FastAPI app in-process, with ``get_db``
    swapped for the rollback session above."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Users. Emails are randomized so a leaked row can never collide with the next
# test's registration.
# ---------------------------------------------------------------------------
def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


DEFAULT_PASSWORD = "correct-horse-battery"

CODE_PATTERN = re.compile(r"verification code is (\d{6})")
RESET_LINK_PATTERN = re.compile(r"(\S+/reset-password)#token=([A-Za-z0-9_-]+)")


class Outbox:
    """Captured emails: ``(to, subject, body)`` tuples."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.messages.append((to, subject, body))

    def latest_code(self, to: str) -> str:
        """The most recent verification code emailed to ``to``."""
        for recipient, _subject, body in reversed(self.messages):
            if recipient == to and (match := CODE_PATTERN.search(body)):
                return match.group(1)
        raise AssertionError(f"no verification code was emailed to {to}")

    def reset_emails(self, to: str) -> list[str]:
        """Bodies of every password reset email sent to ``to``, oldest first."""
        return [
            body
            for recipient, _subject, body in self.messages
            if recipient == to and RESET_LINK_PATTERN.search(body)
        ]

    def latest_reset_token(self, to: str) -> str:
        """The token from the most recent password reset link sent to ``to``."""
        bodies = self.reset_emails(to)
        if not bodies:
            raise AssertionError(f"no password reset link was emailed to {to}")
        return RESET_LINK_PATTERN.search(bodies[-1]).group(2)


@pytest.fixture(autouse=True)
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    """Every test captures mail instead of sending it, no opt-in needed."""
    from app.core import mailer

    box = Outbox()
    monkeypatch.setattr(mailer, "send_email", box.send)
    return box


@pytest.fixture
def no_reset_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests that request resets back to back; the hourly cap still applies."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PASSWORD_RESET_COOLDOWN_SECONDS", 0)


@pytest.fixture
def register(client: AsyncClient) -> Callable:
    """Register a user; returns ``(payload, response)``."""

    async def _register(
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        name: str | None = "Test User",
    ):
        payload = {"email": email or unique_email(), "password": password}
        if name is not None:
            payload["name"] = name
        response = await client.post("/api/v1/auth/register", json=payload)
        return payload, response

    return _register


@pytest.fixture
def login(client: AsyncClient) -> Callable:
    """Log in with the OAuth2 password form; returns the raw response."""

    async def _login(email: str, password: str):
        return await client.post(
            "/api/v1/auth/login", data={"username": email, "password": password}
        )

    return _login


@pytest.fixture
def no_resend_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests that resend back to back; the hourly cap still applies."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "VERIFICATION_RESEND_COOLDOWN_SECONDS", 0)


@pytest.fixture
def verify(client: AsyncClient, outbox: Outbox) -> Callable:
    """Verify ``email`` with the code from the outbox (or an explicit one)."""

    async def _verify(email: str, code: str | None = None):
        return await client.post(
            "/api/v1/auth/verify",
            json={"email": email, "code": code or outbox.latest_code(email)},
        )

    return _verify


@pytest.fixture
async def auth_headers(
    register: Callable, verify: Callable, login: Callable
) -> dict[str, str]:
    """Headers for a freshly registered, verified, logged-in user."""
    payload, response = await register()
    assert response.status_code == 201, response.text
    verified = await verify(payload["email"])
    assert verified.status_code == 200, verified.text
    token_response = await login(payload["email"], payload["password"])
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Storage and queue fakes for the document endpoints. Neither MinIO nor a
# Celery broker is available in CI, and a unit test should not need them.
# ---------------------------------------------------------------------------
class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(self, file, key: str) -> None:
        self.objects[key] = file.read()

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def download_file(self, key: str):
        from io import BytesIO

        return BytesIO(self.objects[key])

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"http://fake/{key}"

    def delete_file(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    from app.api.v1.endpoints import documents

    storage = FakeStorage()
    monkeypatch.setattr(documents, "get_storage", lambda: storage)
    return storage


@pytest.fixture
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> list:
    """Records enqueued job ids instead of talking to Celery."""
    from app.api.v1.endpoints import documents

    enqueued: list = []

    def _enqueue(job_id):
        enqueued.append(job_id)
        return f"task-{len(enqueued)}"

    monkeypatch.setattr(documents, "enqueue_document_processing", _enqueue)
    return enqueued
