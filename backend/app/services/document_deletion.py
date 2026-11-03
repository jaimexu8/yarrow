"""Soft-delete, restore, and permanently wipe documents"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import (
    Document,
    DocumentShare,
    DocumentTag,
    Job,
    Page,
    Region,
    RegionImage,
    RegionTable,
    RegionText,
    Table,
    TableCell,
    User,
    Warning,
)

logger = logging.getLogger(__name__)

# Jobs whose Celery task may still be waiting in the queue.
_UNFINISHED_JOB_STATES = ("queued", "processing")

# Fixed window. Not a user setting.
TRASH_RETENTION_DAYS = 7


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def trash_cutoff(now: datetime | None = None) -> datetime:
    """Documents with deleted_at on or before this instant are expired."""
    return (now or _utcnow()) - timedelta(days=TRASH_RETENTION_DAYS)


def is_trash_expired(deleted_at: datetime | None, now: datetime | None = None) -> bool:
    if deleted_at is None:
        return False
    return deleted_at <= trash_cutoff(now)


class DocumentDeletionError(str, Enum):
    BUSY = "DOCUMENT_BUSY"
    NOT_FOUND = "NOT_FOUND"


class DocumentRestoreError(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    NOT_TRASHED = "NOT_IN_TRASH"
    EXPIRED = "TRASH_EXPIRED"
    BUSY = "DOCUMENT_BUSY"


@dataclass
class DocumentDeletionOutcome:
    """What happened, in terms the endpoint maps to HTTP"""

    error: DocumentDeletionError | None = None
    detail: str | None = None
    # Stored files to remove once the deletion is committed.
    storage_keys: list[str] = field(default_factory=list)
    # Celery tasks to revoke, so a queued job does not start on a document that no longer exists.
    task_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class DocumentRestoreOutcome:
    """What happened restoring a trashed document"""

    error: DocumentRestoreError | None = None
    detail: str | None = None
    document: Document | None = None
    # Set when an expired document is purged instead of restored.
    storage_keys: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


async def delete_document_rows(
    db: AsyncSession, document_ids: Sequence[UUID]
) -> list[str]:
    """Delete documents and every row that depends on them, children first.

    The caller must already hold row locks on the documents, so a worker that
    is mid-way through saving results finishes (or waits) instead of writing
    pages for a document that is being deleted.

    Returns the storage keys of the original files and extracted images.
    """
    if not document_ids:
        return []

    keys = list(
        await db.scalars(
            select(Document.storage_key).where(
                Document.id.in_(document_ids), Document.storage_key.is_not(None)
            )
        )
    )

    page_ids = select(Page.id).where(Page.document_id.in_(document_ids))
    region_ids = select(Region.id).where(Region.page_id.in_(page_ids))
    table_ids = select(Table.id).where(Table.document_id.in_(document_ids))
    region_table_ids = select(RegionTable.id).where(
        or_(
            RegionTable.region_id.in_(region_ids),
            RegionTable.table_id.in_(table_ids),
        )
    )

    keys.extend(
        await db.scalars(
            select(RegionImage.image_key).where(
                RegionImage.region_id.in_(region_ids),
                RegionImage.image_key.is_not(None),
            )
        )
    )

    for statement in (
        delete(TableCell).where(TableCell.region_table_id.in_(region_table_ids)),
        delete(RegionTable).where(RegionTable.id.in_(region_table_ids)),
        delete(RegionText).where(RegionText.region_id.in_(region_ids)),
        delete(RegionImage).where(RegionImage.region_id.in_(region_ids)),
        delete(Region).where(Region.id.in_(region_ids)),
        delete(Warning).where(Warning.page_id.in_(page_ids)),
        delete(Page).where(Page.id.in_(page_ids)),
        delete(Table).where(Table.id.in_(table_ids)),
        delete(Job).where(Job.document_id.in_(document_ids)),
        delete(DocumentShare).where(DocumentShare.document_id.in_(document_ids)),
        delete(DocumentTag).where(DocumentTag.document_id.in_(document_ids)),
        delete(Document).where(Document.id.in_(document_ids)),
    ):
        await db.execute(statement)

    return keys


async def _lock_document(
    db: AsyncSession, document_id: UUID
) -> Document | None:
    return (
        await db.execute(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _unfinished_task_ids(db: AsyncSession, document_id: UUID) -> list[str]:
    return list(
        await db.scalars(
            select(Job.celery_task_id).where(
                Job.document_id == document_id,
                Job.status.in_(_UNFINISHED_JOB_STATES),
                Job.celery_task_id.is_not(None),
            )
        )
    )


async def _refund_quota(db: AsyncSession, owner_id: UUID, size: int) -> None:
    # A single UPDATE, which takes the same row lock that upload's quota
    # check holds, so the refund and a concurrent upload cannot overwrite
    # each other's total.
    await db.execute(
        update(User)
        .where(User.id == owner_id)
        .values(
            storage_used_bytes=func.greatest(
                func.coalesce(User.storage_used_bytes, 0) - size, 0
            )
        )
        .execution_options(synchronize_session=False)
    )


async def _wipe_locked_document(
    db: AsyncSession, document: Document
) -> tuple[list[str], list[str]]:
    """Remove rows and refund quota. Caller holds the document lock and commits."""
    document_id = document.id
    task_ids = await _unfinished_task_ids(db, document_id)
    owner_id = document.owner_id
    size = document.file_size_bytes or 0
    keys = await delete_document_rows(db, [document_id])
    await _refund_quota(db, owner_id, size)
    return keys, task_ids


async def _stop_unfinished_jobs(db: AsyncSession, document: Document) -> list[str]:
    """Revoke later; mark queued/processing work canceled so the worker stops."""
    task_ids = await _unfinished_task_ids(db, document.id)
    jobs = (
        (
            await db.execute(
                select(Job).where(
                    Job.document_id == document.id,
                    Job.status.in_(_UNFINISHED_JOB_STATES),
                )
            )
        )
        .scalars()
        .all()
    )
    now = _utcnow()
    for job in jobs:
        job.status = "canceled"
        job.updated_at = now
    if document.status in _UNFINISHED_JOB_STATES:
        document.status = "canceled"
        document.error_message = None
    return task_ids


async def delete_document(
    db: AsyncSession, document_id: UUID
) -> DocumentDeletionOutcome:
    """Permanently delete one document, its extracted data and jobs, and refund
    its size to the owner's storage quota. Commits on success.

    Used by the 7-day purge path. Queued and processing documents can be
    wiped too. Their tasks are returned for revoking; a worker that picks
    one up anyway finds its job gone and stops.
    """
    try:
        await db.execute(lock_timeout_statement())
        document = await _lock_document(db, document_id)
        if document is None:
            # Deleted by another request
            return DocumentDeletionOutcome()

        keys, task_ids = await _wipe_locked_document(db, document)
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.BUSY,
                detail="This document is being saved by processing; try again shortly.",
            )
        raise

    await db.commit()
    return DocumentDeletionOutcome(storage_keys=keys, task_ids=task_ids)


async def soft_delete_document(
    db: AsyncSession, document_id: UUID
) -> DocumentDeletionOutcome:
    """Move a document to trash. Extracted rows and files stay; quota is not
    refunded, so a later restore cannot fail the storage cap.

    Unfinished jobs are marked canceled and their tasks are returned for
    revoking, so processing stops. If a worker holds the row lock, the
    request answers BUSY after a short wait.
    """
    try:
        await db.execute(lock_timeout_statement())
        document = await _lock_document(db, document_id)
        if document is None:
            return DocumentDeletionOutcome()
        if document.deleted_at is not None:
            return DocumentDeletionOutcome()

        task_ids = await _stop_unfinished_jobs(db, document)
        document.deleted_at = _utcnow()
        document.updated_at = document.deleted_at
        # So a later upload with the same client id is not blocked by the
        # unique owner+upload-id index while this row sits in trash.
        document.client_upload_id = None
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.BUSY,
                detail="This document is being saved by processing; try again shortly.",
            )
        raise

    await db.commit()
    return DocumentDeletionOutcome(task_ids=task_ids)


async def restore_document(
    db: AsyncSession, document_id: UUID, owner_id: UUID
) -> DocumentRestoreOutcome:
    """Put a trashed document back in the library if it is still within 7 days.

    Older than that: permanently wipe it (rows, files, quota refund) and
    report EXPIRED. Missing or not owned: NOT_FOUND.
    """
    try:
        await db.execute(lock_timeout_statement())
        document = await _lock_document(db, document_id)
        if document is None or document.owner_id != owner_id:
            return DocumentRestoreOutcome(
                error=DocumentRestoreError.NOT_FOUND,
                detail="Document not found",
            )
        if document.deleted_at is None:
            return DocumentRestoreOutcome(
                error=DocumentRestoreError.NOT_TRASHED,
                detail="This document is not in trash.",
            )
        if is_trash_expired(document.deleted_at):
            keys, task_ids = await _wipe_locked_document(db, document)
            await db.commit()
            return DocumentRestoreOutcome(
                error=DocumentRestoreError.EXPIRED,
                detail=(
                    "This document has been in trash for more than "
                    f"{TRASH_RETENTION_DAYS} days and was permanently deleted."
                ),
                storage_keys=keys,
                task_ids=task_ids,
            )

        document.deleted_at = None
        document.updated_at = _utcnow()
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return DocumentRestoreOutcome(
                error=DocumentRestoreError.BUSY,
                detail="This document is being saved by processing; try again shortly.",
            )
        raise

    await db.commit()
    await db.refresh(document)
    return DocumentRestoreOutcome(document=document)


async def purge_expired_documents(db: AsyncSession) -> DocumentDeletionOutcome:
    """Permanently delete every document whose trash window has ended.

    Cheap to call from list and restore: most of the time there is nothing
    to wipe. A document a worker is saving is skipped (BUSY) and tried again
    on the next list.
    """
    expired_ids = list(
        await db.scalars(
            select(Document.id).where(
                Document.deleted_at.is_not(None),
                Document.deleted_at <= trash_cutoff(),
            )
        )
    )
    keys: list[str] = []
    task_ids: list[str] = []
    for document_id in expired_ids:
        outcome = await _purge_if_expired(db, document_id)
        if outcome.ok:
            keys.extend(outcome.storage_keys)
            task_ids.extend(outcome.task_ids)
    return DocumentDeletionOutcome(storage_keys=keys, task_ids=task_ids)


async def _purge_if_expired(
    db: AsyncSession, document_id: UUID
) -> DocumentDeletionOutcome:
    """Wipe one document only if it is still expired when the row is locked."""
    try:
        await db.execute(lock_timeout_statement())
        document = await _lock_document(db, document_id)
        if document is None or not is_trash_expired(document.deleted_at):
            return DocumentDeletionOutcome()
        keys, task_ids = await _wipe_locked_document(db, document)
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.BUSY,
                detail="This document is being saved by processing; try again shortly.",
            )
        raise

    await db.commit()
    return DocumentDeletionOutcome(storage_keys=keys, task_ids=task_ids)


async def permanently_delete_trashed(
    db: AsyncSession, document_id: UUID, owner_id: UUID
) -> DocumentDeletionOutcome:
    """Wipe a trashed document. Live or missing documents are not found."""
    try:
        await db.execute(lock_timeout_statement())
        document = await _lock_document(db, document_id)
        if (
            document is None
            or document.owner_id != owner_id
            or document.deleted_at is None
        ):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.NOT_FOUND,
                detail="Document not found",
            )
        keys, task_ids = await _wipe_locked_document(db, document)
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return DocumentDeletionOutcome(
                error=DocumentDeletionError.BUSY,
                detail="This document is being saved by processing; try again shortly.",
            )
        raise

    await db.commit()
    return DocumentDeletionOutcome(storage_keys=keys, task_ids=task_ids)


async def empty_user_trash(
    db: AsyncSession, owner_id: UUID
) -> DocumentDeletionOutcome:
    """Permanently delete every trashed document this user owns."""
    ids = list(
        await db.scalars(
            select(Document.id).where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_not(None),
            )
        )
    )
    keys: list[str] = []
    task_ids: list[str] = []
    for document_id in ids:
        outcome = await permanently_delete_trashed(db, document_id, owner_id)
        if outcome.ok:
            keys.extend(outcome.storage_keys)
            task_ids.extend(outcome.task_ids)
    return DocumentDeletionOutcome(storage_keys=keys, task_ids=task_ids)


def delete_stored_objects(storage, keys: Iterable[str]) -> list[str]:
    """Remove stored files, returning the keys that could not be removed"""
    failed = []
    for key in keys:
        try:
            storage.delete_file(key)
        except Exception:
            logger.exception(f"Failed to delete storage object {key}")
            failed.append(key)
    return failed
