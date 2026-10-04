"""Permanently delete documents and everything extracted from them"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import (
    Document,
    DocumentShare,
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


class DocumentDeletionError(str, Enum):
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
        delete(Document).where(Document.id.in_(document_ids)),
    ):
        await db.execute(statement)

    return keys


async def delete_document(
    db: AsyncSession, document_id: UUID
) -> DocumentDeletionOutcome:
    """Delete one document, its extracted data and jobs, and refund its size
    to the owner's storage quota. Commits on success.

    Queued and processing documents can be deleted too. Their tasks are
    returned for revoking; a worker that picks one up anyway finds its job
    gone and stops.
    """
    try:
        await db.execute(lock_timeout_statement())
        document = (
            await db.execute(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if document is None:
            # Deleted by another request
            return DocumentDeletionOutcome()

        task_ids = list(
            await db.scalars(
                select(Job.celery_task_id).where(
                    Job.document_id == document_id,
                    Job.status.in_(_UNFINISHED_JOB_STATES),
                    Job.celery_task_id.is_not(None),
                )
            )
        )
        owner_id = document.owner_id
        size = document.file_size_bytes or 0

        keys = await delete_document_rows(db, [document_id])

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
