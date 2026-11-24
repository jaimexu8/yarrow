"""Nightly sweep of stored objects that no longer have a database reference.

Every delete is driven by an exact key read from storage and checked against
Postgres. Nothing is deleted by prefix alone, except the two temporary page
prefixes that are never referenced by a row. Objects newer than the grace
window are left alone: prepare_pages uploads thumbnails before it writes the
Page row, so a recent object with no row may still be in use.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import redis
from sqlalchemy import select
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionImage,
    UploadChunk,
    UploadSession,
)
from yarrow_db.session import session_scope
from yarrow_storage import DOCUMENTS_PREFIX, UPLOADS_PREFIX, StoredObject, get_storage

from app.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

_DOCUMENT_KEY = re.compile(rf"^{DOCUMENTS_PREFIX}([0-9a-fA-F-]{{32,36}})/(.+)$")
_UPLOAD_KEY = re.compile(rf"^{UPLOADS_PREFIX}([0-9a-fA-F-]{{32,36}})/chunks/")
_LOCK_KEY = "storage_sweep:lock"
_TEMP_MARKERS = ("/rasters/", "/text_layer/")
_QUERY_CHUNK = 500


@dataclass
class SweepStats:
    scanned: int = 0
    recent: int = 0
    kept: int = 0
    deleted: int = 0
    would_delete: int = 0
    failed: int = 0
    foreign: int = 0

    def log(self) -> None:
        logger.info(
            "Storage sweep: scanned=%d recent=%d kept=%d deleted=%d "
            "would_delete=%d failed=%d foreign=%d",
            self.scanned,
            self.recent,
            self.kept,
            self.deleted,
            self.would_delete,
            self.failed,
            self.foreign,
        )


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _chunks(values: Sequence[UUID], size: int) -> Iterator[list[UUID]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _group(
    objects: Iterable[StoredObject],
) -> tuple[dict[UUID, list[StoredObject]], dict[UUID, list[StoredObject]], int]:
    """Bucket listing results by the UUID in their key."""
    documents: dict[UUID, list[StoredObject]] = defaultdict(list)
    uploads: dict[UUID, list[StoredObject]] = defaultdict(list)
    foreign = 0
    for obj in objects:
        match = _DOCUMENT_KEY.match(obj.key)
        if match is not None:
            document_id = _parse_uuid(match.group(1))
            if document_id is not None:
                documents[document_id].append(obj)
                continue

        match = _UPLOAD_KEY.match(obj.key)
        if match is not None:
            session_id = _parse_uuid(match.group(1))
            if session_id is not None:
                uploads[session_id].append(obj)
                continue

        foreign += 1
    return documents, uploads, foreign


def _document_references(session, ids: list[UUID]) -> tuple[set[UUID], set[str]]:
    """Live document ids and every storage key referenced by their rows."""
    rows = session.execute(
        select(Document.id, Document.storage_key).where(Document.id.in_(ids))
    ).all()
    existing = {row.id for row in rows}
    live = {row.storage_key for row in rows if row.storage_key}

    for column in (Page.thumbnail_key, Page.page_image_key):
        live.update(
            session.scalars(
                select(column).where(Page.document_id.in_(ids), column.is_not(None))
            )
        )

    live.update(
        session.scalars(
            select(RegionImage.image_key)
            .join(Region, RegionImage.region_id == Region.id)
            .join(Page, Region.page_id == Page.id)
            .where(Page.document_id.in_(ids), RegionImage.image_key.is_not(None))
        )
    )
    return existing, live


def _upload_references(session, ids: list[UUID]) -> tuple[set[UUID], set[str]]:
    existing = set(
        session.scalars(select(UploadSession.id).where(UploadSession.id.in_(ids)))
    )
    live = set(
        session.scalars(
            select(UploadChunk.storage_key).where(UploadChunk.session_id.in_(ids))
        )
    )
    return existing, live


def _orphans(
    objects: Iterable[StoredObject],
    owner_exists: bool,
    live: set[str],
    cutoff: datetime,
    stats: SweepStats,
    *,
    temporary_allowed: bool,
) -> list[str]:
    """Pick keys to remove, counting every object exactly once."""
    orphans: list[str] = []
    for obj in objects:
        if obj.last_modified > cutoff:
            stats.recent += 1
            continue
        if (
            owner_exists
            and obj.key in live
            and not (
                temporary_allowed and any(marker in obj.key for marker in _TEMP_MARKERS)
            )
        ):
            stats.kept += 1
            continue
        orphans.append(obj.key)
    return orphans


def _delete(keys: Iterable[str], stats: SweepStats, max_deletes: int | None) -> None:
    storage = get_storage()
    for key in keys:
        if (
            max_deletes is not None
            and stats.deleted + stats.would_delete >= max_deletes
        ):
            logger.warning("Storage sweep hit the per-run delete cap")
            return
        if settings.STORAGE_SWEEP_DRY_RUN:
            logger.info("Storage sweep would delete %s", key)
            stats.would_delete += 1
            continue
        try:
            storage.delete_file(key)
            stats.deleted += 1
        except Exception:
            logger.exception("Storage sweep could not delete %s", key)
            stats.failed += 1


def _sweep() -> SweepStats:
    stats = SweepStats()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.STORAGE_SWEEP_GRACE_HOURS)
    storage = get_storage()

    objects: list[StoredObject] = []
    for prefix in (DOCUMENTS_PREFIX, UPLOADS_PREFIX):
        objects.extend(storage.list_objects(prefix))
    stats.scanned = len(objects)

    documents, uploads, stats.foreign = _group(objects)
    orphans: list[str] = []

    with session_scope() as session:
        document_ids = list(documents)
        for ids in _chunks(document_ids, _QUERY_CHUNK):
            existing, live = _document_references(session, ids)
            for document_id in ids:
                orphans.extend(
                    _orphans(
                        documents[document_id],
                        document_id in existing,
                        live,
                        cutoff,
                        stats,
                        temporary_allowed=True,
                    )
                )

        upload_ids = list(uploads)
        for ids in _chunks(upload_ids, _QUERY_CHUNK):
            existing, live = _upload_references(session, ids)
            for session_id in ids:
                orphans.extend(
                    _orphans(
                        uploads[session_id],
                        session_id in existing,
                        live,
                        cutoff,
                        stats,
                        temporary_allowed=False,
                    )
                )

    _delete(orphans, stats, settings.STORAGE_SWEEP_MAX_DELETES)
    return stats


def _lock_client():
    try:
        return redis.from_url(settings.PAGE_COUNTERS_REDIS_URL, socket_timeout=2)
    except Exception:
        logger.exception("Storage sweep lock store is unreachable")
        return None


@celery_app.task(
    name="app.tasks.storage_sweep.sweep_orphaned_objects",
    queue="default",
    soft_time_limit=3600,
    time_limit=3660,
    acks_late=False,
)
def sweep_orphaned_objects() -> dict:
    """Delete storage objects that have no matching row, oldest ones first."""
    if not settings.STORAGE_SWEEP_ENABLED:
        logger.info("Storage sweep is disabled")
        return vars(SweepStats())

    client = _lock_client()
    locked = False
    if client is not None:
        try:
            locked = bool(
                client.set(
                    _LOCK_KEY,
                    "1",
                    nx=True,
                    ex=settings.STORAGE_SWEEP_LOCK_SECONDS,
                )
            )
        except Exception:
            logger.exception("Could not acquire the storage sweep lock")

    if client is not None and not locked:
        logger.info("Another storage sweep is already running")
        return vars(SweepStats())

    try:
        stats = _sweep()
    finally:
        if locked:
            try:
                client.delete(_LOCK_KEY)
            except Exception:
                logger.exception("Could not release the storage sweep lock")

    stats.log()
    return vars(stats)
