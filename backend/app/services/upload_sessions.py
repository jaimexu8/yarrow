"""Temporary chunk storage and cleanup for resumable uploads (US-80).

Chunks are kept as ordinary objects in the object store, under their own
prefix, rather than on a backend container's disk: compose (and any real
deployment) can run several backend processes, and the next chunk of an
upload, or its finalize, may land on a different one than the last.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import UploadChunk, UploadSession
from yarrow_storage import StorageProvider

from app.core.config import settings

logger = logging.getLogger(__name__)

# Sessions swept per call, so one request never pays for a large backlog.
SWEEP_BATCH = 50


def utcnow() -> datetime:
    """Naive UTC, like every other timestamp column."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_expiry(now: datetime | None = None) -> datetime:
    """When a session touched now stops being resumable."""
    return (now or utcnow()) + timedelta(hours=settings.UPLOAD_SESSION_TTL_HOURS)


def is_expired(upload: UploadSession, now: datetime | None = None) -> bool:
    return upload.expires_at <= (now or utcnow())


def chunk_key(session_id: UUID, index: int, sha256: str) -> str:
    """Keyed by session, index and content.

    The content hash in the key means two racing requests carrying different
    bytes for the same index write different objects: the loser's object is
    removed and can never overwrite the confirmed one.
    """
    return f"uploads/{session_id}/chunks/{index:06d}-{sha256[:16]}"


def delete_objects(storage: StorageProvider, keys: Iterable[str]) -> list[str]:
    """Remove objects, returning the keys that could not be removed."""
    failed = []
    for key in keys:
        try:
            storage.delete_file(key)
        except Exception:
            logger.exception(f"Deleting upload chunk {key} failed")
            failed.append(key)
    return failed


async def discard_chunks(
    db: AsyncSession, storage: StorageProvider, session_id: UUID
) -> None:
    """Delete a session's chunk objects, then the rows of those that went.

    A row whose object could not be deleted is kept, so the sweeper tries
    again when the session expires instead of leaving an untracked object.
    """
    chunks = list(
        await db.scalars(
            select(UploadChunk).where(UploadChunk.session_id == session_id)
        )
    )
    if not chunks:
        return
    failed = set(
        await run_in_threadpool(
            delete_objects, storage, [chunk.storage_key for chunk in chunks]
        )
    )
    gone = [chunk.id for chunk in chunks if chunk.storage_key not in failed]
    if gone:
        await db.execute(delete(UploadChunk).where(UploadChunk.id.in_(gone)))
    await db.commit()


async def purge_expired_upload_sessions(
    db: AsyncSession, storage_factory, now: datetime | None = None
) -> int:
    """Delete sessions past their retention window, chunks and all.

    Cheap to call opportunistically (on new uploads and on the document list,
    like the trash purge): the expiry index makes "nothing to do" one small
    query. Rows another request has locked are skipped and swept next time.
    ``storage_factory`` is only called when there is something to delete.
    Returns the number of sessions removed.
    """
    expired = list(
        await db.scalars(
            select(UploadSession.id)
            .where(UploadSession.expires_at <= (now or utcnow()))
            .order_by(UploadSession.expires_at)
            .limit(SWEEP_BATCH)
            .with_for_update(skip_locked=True)
        )
    )
    if not expired:
        return 0

    rows = (
        await db.execute(
            select(UploadChunk.session_id, UploadChunk.storage_key).where(
                UploadChunk.session_id.in_(expired)
            )
        )
    ).all()
    failed = set()
    if rows:
        failed = set(
            await run_in_threadpool(
                delete_objects, storage_factory(), [row.storage_key for row in rows]
            )
        )
    # A session with an object that would not delete stays for the next
    # sweep; the rest go, and their chunk rows cascade with them.
    keep = {row.session_id for row in rows if row.storage_key in failed}
    removable = [session_id for session_id in expired if session_id not in keep]
    if removable:
        await db.execute(delete(UploadSession).where(UploadSession.id.in_(removable)))
    await db.commit()
    return len(removable)
