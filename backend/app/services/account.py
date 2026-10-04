import logging
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
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
from yarrow_storage import get_storage

from app.core.security import get_password_hash
from app.schemas.auth import UserUpdate

logger = logging.getLogger(__name__)


class AccountUpdateError(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    BUSY = "BUSY"
    EMAIL_TAKEN = "EMAIL_TAKEN"


class AccountDeletionError(str, Enum):
    BUSY = "BUSY"


@dataclass
class AccountDeletionOutcome:
    """What happened during account deletion in terms the endpoint maps to HTTP"""

    error: AccountDeletionError | None = None
    detail: str | None = None
    documents_deleted: int = 0
    # Storage objects that could not be removed after the rows were committed.
    orphaned_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

@dataclass
class AccountUpdateOutcome:
    """What happened during account update in terms the endpoint maps to HTTP"""

    error: AccountUpdateError | None = None
    detail: str | None = None
    ok: bool = False

async def _delete_rows(db: AsyncSession, user_id: UUID) -> tuple[int, list[str]]:
    """Delete everything the user owns, children first.

    Queued and processing documents are deleted too. Their tasks are left in
    the queue, and the worker ends a job quietly once its rows are gone.
    """

    await db.execute(lock_timeout_statement())

    # Waits for a process that is mid-way through saving results, rather than deleting pages it is about to reference.
    documents = (
        await db.execute(
            select(Document.id, Document.storage_key)
            .where(Document.owner_id == user_id)
            .with_for_update()
        )
    ).all()

    document_ids = [row.id for row in documents]
    keys = [row.storage_key for row in documents if row.storage_key]

    if document_ids:
        # Delete all dependent rows for the user's documents.

        page_ids = select(Page.id).where(Page.document_id.in_(document_ids))
        region_ids = select(Region.id).where(Region.page_id.in_(page_ids))
        table_ids = select(Table.id).where(Table.document_id.in_(document_ids))
        region_table_ids = select(RegionTable.id).where(
            or_(
                RegionTable.region_id.in_(region_ids),
                RegionTable.table_id.in_(table_ids),
            )
        )

        image_keys = await db.scalars(
            select(RegionImage.image_key).where(
                RegionImage.region_id.in_(region_ids),
                RegionImage.image_key.is_not(None),
            )
        )
        keys.extend(image_keys)

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
        ):
            await db.execute(statement)

    # Delete all document shares involving the user, either as the owner of the document or as the recipient of a shared document.
    share_filter = DocumentShare.shared_with_user_id == user_id
    if document_ids:
        share_filter = or_(share_filter, DocumentShare.document_id.in_(document_ids))
    await db.execute(delete(DocumentShare).where(share_filter))

    # Finally, delete the user's documents and the user account itself.
    if document_ids:
        await db.execute(delete(Document).where(Document.id.in_(document_ids)))
    await db.execute(delete(User).where(User.id == user_id))
    return len(document_ids), keys


def _delete_objects(keys: list[str]) -> list[str]:
    """Remove stored files, returning the keys that could not be removed."""
    storage = get_storage()
    failed = []
    for key in keys:
        try:
            storage.delete_file(key)
        except Exception:
            logger.exception("Failed to delete storage object %s", key)
            failed.append(key)
    return failed


async def delete_account(db: AsyncSession, user_id: UUID) -> AccountDeletionOutcome:
    """Permanently delete a user, their documents, and their stored files"""
    try:
        documents_deleted, keys = await _delete_rows(db, user_id)
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return AccountDeletionOutcome(
                error=AccountDeletionError.BUSY,
                detail="A document is being saved by processing; try again shortly",
            )
        raise

    await db.commit()

    orphaned = await run_in_threadpool(_delete_objects, keys) if keys else []
    return AccountDeletionOutcome(
        documents_deleted=documents_deleted,
        orphaned_keys=orphaned,
    )

async def update_account(db: AsyncSession, user_id: UUID, payload: UserUpdate) -> AccountUpdateOutcome:
    """Update a user's account information."""
    try:
        user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
        if not user:
            return AccountUpdateOutcome(ok=False, error=AccountUpdateError.NOT_FOUND, detail="User not found")

        if payload.name is not None:
            user.name = payload.name
        if payload.email is not None:
            user.email = payload.email
        if payload.password is not None:
            user.hashed_password = get_password_hash(payload.password)

        await db.commit()
        return AccountUpdateOutcome(ok=True)
    except IntegrityError:
        # users.email is unique: another account already uses the new email.
        await db.rollback()
        return AccountUpdateOutcome(
            ok=False,
            error=AccountUpdateError.EMAIL_TAKEN,
            detail="An account with that email already exists",
        )
    except DBAPIError as exc:
        await db.rollback()
        if is_lock_timeout(exc):
            return AccountUpdateOutcome(
                ok=False,
                error=AccountUpdateError.BUSY,
                detail="An error occurred. Please try again shortly.",
            )
        raise