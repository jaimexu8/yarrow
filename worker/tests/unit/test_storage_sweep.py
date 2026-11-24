"""Unit tests for the nightly storage sweep."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from yarrow_db.models import (
    Base,
    Document,
    Page,
    Region,
    RegionImage,
    UploadChunk,
    UploadSession,
)
from yarrow_storage import StoredObject

from app.tasks import storage_sweep

OLD = datetime.now(UTC) - timedelta(days=10)
RECENT = datetime.now(UTC) - timedelta(hours=1)


class FakeStore:
    def __init__(self, **objects):
        self.objects = dict(objects)

    def list_objects(self, prefix):
        return [
            StoredObject(key=key, last_modified=when, size=len(value))
            for key, (when, value) in self.objects.items()
            if key.startswith(prefix)
        ]

    def delete_file(self, key):
        self.objects.pop(key, None)


def obj(when=OLD, value=b"x"):
    return (when, value)


def document(document_id, storage_key):
    return Document(
        id=document_id,
        owner_id=uuid4(),
        filename="a.pdf",
        file_size_bytes=1,
        file_type="application/pdf",
        storage_key=storage_key,
    )


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(storage_sweep, "session_scope", scope)
    yield factory
    engine.dispose()


@pytest.fixture
def sweep(monkeypatch):
    def run(store, *, dry_run=False, max_deletes=None):
        monkeypatch.setattr(storage_sweep, "get_storage", lambda: store)
        monkeypatch.setattr(storage_sweep.settings, "STORAGE_SWEEP_DRY_RUN", dry_run)
        monkeypatch.setattr(
            storage_sweep.settings, "STORAGE_SWEEP_MAX_DELETES", max_deletes
        )
        return storage_sweep._sweep()

    return run


def test_every_object_of_a_missing_document_is_deleted(db, sweep):
    document_id = uuid4()
    keys = [
        f"documents/{document_id}/original",
        f"documents/{document_id}/thumbnails/1.webp",
        f"documents/{document_id}/pages/1.png",
        f"documents/{document_id}/figures/{uuid4()}.png",
    ]
    store = FakeStore(**{key: obj() for key in keys})

    stats = sweep(store)

    assert stats.deleted == len(keys)
    assert store.objects == {}


def test_live_document_keeps_referenced_keys_and_drops_the_rest(db, sweep):
    document_id = uuid4()
    page_id = uuid4()
    region_id = uuid4()
    original = f"documents/{document_id}/original"
    thumbnail = f"documents/{document_id}/thumbnails/1.webp"
    page_image = f"documents/{document_id}/pages/1.png"
    figure = f"documents/{document_id}/figures/{region_id}.png"
    extra = f"documents/{document_id}/thumbnails/2.webp"

    with db() as session:
        session.add(document(document_id, original))
        session.add(
            Page(
                id=page_id,
                document_id=document_id,
                page_number=1,
                thumbnail_key=thumbnail,
                page_image_key=page_image,
            )
        )
        session.add(Region(id=region_id, page_id=page_id, page_number=1))
        session.add(RegionImage(region_id=region_id, image_key=figure))
        session.commit()

    store = FakeStore(
        **{key: obj() for key in (original, thumbnail, page_image, figure, extra)}
    )

    stats = sweep(store)

    assert stats.kept == 4
    assert stats.deleted == 1
    assert extra not in store.objects
    assert set(store.objects) == {original, thumbnail, page_image, figure}


def test_recent_orphans_are_left_for_the_next_run(db, sweep):
    document_id = uuid4()
    key = f"documents/{document_id}/thumbnails/1.webp"
    store = FakeStore(**{key: obj(when=RECENT)})

    stats = sweep(store)

    assert stats.recent == 1
    assert stats.deleted == 0
    assert key in store.objects


def test_temporary_prefixes_are_removed_for_a_live_document(db, sweep):
    document_id = uuid4()
    thumbnail = f"documents/{document_id}/thumbnails/1.webp"
    original = f"documents/{document_id}/original"
    with db() as session:
        session.add(document(document_id, original))
        session.add(
            Page(
                id=uuid4(),
                document_id=document_id,
                page_number=1,
                thumbnail_key=thumbnail,
            )
        )
        session.commit()

    rasters = f"documents/{document_id}/rasters/1.png"
    text_layer = f"documents/{document_id}/text_layer/1.json"
    store = FakeStore(
        **{
            original: obj(),
            thumbnail: obj(),
            rasters: obj(),
            text_layer: obj(),
        }
    )

    stats = sweep(store)

    assert stats.deleted == 2
    assert set(store.objects) == {original, thumbnail}


def test_upload_chunks_follow_their_session(db, sweep):
    live_session = uuid4()
    gone_session = uuid4()
    live_chunk = f"uploads/{live_session}/chunks/000000-abcdef0123456789"
    stale_chunk = f"uploads/{live_session}/chunks/000001-abcdef0123456789"
    gone_chunk = f"uploads/{gone_session}/chunks/000000-abcdef0123456789"
    with db() as session:
        session.add(
            UploadSession(
                id=live_session,
                owner_id=uuid4(),
                filename="a.pdf",
                total_size_bytes=1,
                chunk_size_bytes=1,
                chunk_count=2,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.add(
            UploadChunk(
                session_id=live_session,
                chunk_index=0,
                size_bytes=1,
                sha256="abcdef0123456789",
                storage_key=live_chunk,
            )
        )
        session.commit()

    store = FakeStore(**{live_chunk: obj(), stale_chunk: obj(), gone_chunk: obj()})

    stats = sweep(store)

    assert stats.kept == 1
    assert stats.deleted == 2
    assert set(store.objects) == {live_chunk}


def test_dry_run_reports_without_deleting(db, sweep):
    document_id = uuid4()
    key = f"documents/{document_id}/thumbnails/1.webp"
    store = FakeStore(**{key: obj()})

    stats = sweep(store, dry_run=True)

    assert stats.would_delete == 1
    assert stats.deleted == 0
    assert key in store.objects


def test_delete_cap_stops_the_run(db, sweep):
    document_id = uuid4()
    store = FakeStore(
        **{f"documents/{document_id}/thumbnails/{n}.webp": obj() for n in range(1, 6)}
    )

    stats = sweep(store, max_deletes=2)

    assert stats.deleted == 2
    assert len(store.objects) == 3


def test_task_skips_when_another_sweep_holds_the_lock(monkeypatch):
    class LockedClient:
        def set(self, *args, **kwargs):
            return False

        def delete(self, *args):
            raise AssertionError("a lock we did not acquire must not be released")

    def must_not_run():
        raise AssertionError("the sweep must not run while locked")

    monkeypatch.setattr(storage_sweep, "_lock_client", lambda: LockedClient())
    monkeypatch.setattr(storage_sweep, "_sweep", must_not_run)
    monkeypatch.setattr(storage_sweep.settings, "STORAGE_SWEEP_ENABLED", True)

    assert storage_sweep.sweep_orphaned_objects() == vars(storage_sweep.SweepStats())


def test_unparsable_keys_are_counted_and_left_alone(db, sweep):
    key = "documents/not-a-uuid/thing.webp"
    store = FakeStore(**{key: obj()})

    stats = sweep(store)

    assert stats.foreign == 1
    assert stats.scanned == 1
    assert stats.deleted == 0
    assert key in store.objects
