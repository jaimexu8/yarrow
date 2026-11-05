from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from yarrow_db.models import Document, UploadSession, User

from app.core.config import settings
from app.services.upload_sessions import utcnow

ME_STORAGE = "/api/v1/auth/me/storage"
QUOTA = 1000


@pytest.fixture(autouse=True)
def small_quota(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_QUOTA_BYTES", QUOTA)


async def _current_user(client, auth_headers, db_session) -> User:
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    user = await db_session.get(User, UUID(me["id"]))
    assert user is not None
    return user


def _upload_session(owner_id, size, *, status="processing", expires_in_hours=1):
    return UploadSession(
        id=uuid4(),
        owner_id=owner_id,
        filename="large.pdf",
        total_size_bytes=size,
        chunk_size_bytes=size,
        chunk_count=1,
        status=status,
        expires_at=utcnow() + timedelta(hours=expires_in_hours),
    )


def _document(owner_id, size, *, trashed):
    document_id = uuid4()
    return Document(
        id=document_id,
        owner_id=owner_id,
        filename="doc.pdf",
        file_size_bytes=size,
        file_type="application/pdf",
        storage_key=f"documents/{document_id}/original.pdf",
        deleted_at=utcnow() if trashed else None,
    )


async def test_requires_authentication(client):
    response = await client.get(ME_STORAGE)
    assert response.status_code == 401


async def test_reports_usage_for_a_new_user(client, auth_headers):
    response = await client.get(ME_STORAGE, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "used_bytes": 0,
        "reserved_bytes": 0,
        "trash_bytes": 0,
        "quota_bytes": QUOTA,
        "remaining_bytes": QUOTA,
        "used_ratio": 0.0,
        "level": "ok",
        "warning_threshold": settings.STORAGE_WARNING_RATIO,
        "critical_threshold": settings.STORAGE_CRITICAL_RATIO,
    }


@pytest.mark.parametrize(
    ("used_bytes", "level"),
    [
        (790, "ok"),
        (799, "ok"),
        (800, "warning"),
        (949, "warning"),
        (950, "critical"),
        (999, "critical"),
        (1000, "full"),
        (1100, "full"),
    ],
)
async def test_level_boundaries(client, auth_headers, db_session, used_bytes, level):
    user = await _current_user(client, auth_headers, db_session)
    user.storage_used_bytes = used_bytes
    await db_session.commit()

    data = (await client.get(ME_STORAGE, headers=auth_headers)).json()

    assert data["level"] == level
    assert data["used_bytes"] == used_bytes
    assert data["used_ratio"] == used_bytes / QUOTA
    assert data["remaining_bytes"] == max(QUOTA - used_bytes, 0)


async def test_unfinished_uploads_are_reserved(client, auth_headers, db_session):
    user = await _current_user(client, auth_headers, db_session)
    user.storage_used_bytes = 700
    db_session.add_all(
        [
            _upload_session(user.id, 150),
            _upload_session(user.id, 60, expires_in_hours=-1),
            _upload_session(user.id, 40, status="completed"),
        ]
    )
    await db_session.commit()

    data = (await client.get(ME_STORAGE, headers=auth_headers)).json()

    assert data["used_bytes"] == 700
    assert data["reserved_bytes"] == 150
    assert data["remaining_bytes"] == 150
    assert data["used_ratio"] == 0.85
    assert data["level"] == "warning"


async def test_trashed_documents_are_reported(client, auth_headers, db_session):
    user = await _current_user(client, auth_headers, db_session)
    user.storage_used_bytes = 300
    db_session.add_all(
        [
            _document(user.id, 120, trashed=True),
            _document(user.id, 80, trashed=True),
            _document(user.id, 100, trashed=False),
        ]
    )
    await db_session.commit()

    data = (await client.get(ME_STORAGE, headers=auth_headers)).json()

    assert data["trash_bytes"] == 200
    assert data["used_bytes"] == 300
    assert data["remaining_bytes"] == 700


async def test_other_users_data_is_never_included(client, auth_headers, db_session):
    other = User(
        id=uuid4(),
        email="storage-other@example.com",
        hashed_password="not-a-real-hash",
        storage_used_bytes=900,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add_all(
        [
            _upload_session(other.id, 50),
            _document(other.id, 400, trashed=True),
        ]
    )
    await db_session.commit()

    data = (await client.get(ME_STORAGE, headers=auth_headers)).json()

    assert data["used_bytes"] == 0
    assert data["reserved_bytes"] == 0
    assert data["trash_bytes"] == 0
    assert data["remaining_bytes"] == QUOTA
    assert data["level"] == "ok"
