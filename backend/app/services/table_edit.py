import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from itertools import pairwise
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import Document, Region, RegionTable, Table
from yarrow_db.utils.table_utils import (
    is_consecutive,
    merge_consecutive_tables,
    merge_two_tables,
    ordered_parts,
    split_consecutive_tables,
    split_table,
)

logger = logging.getLogger(__name__)


class MutationError(str, Enum):
    DOCUMENT_NOT_PROCESSED = "DOCUMENT_NOT_PROCESSED"
    DOCUMENT_BUSY = "DOCUMENT_BUSY"
    TABLE_NOT_FOUND = "TABLE_NOT_FOUND"
    TABLES_NOT_CONSECUTIVE = "TABLES_NOT_CONSECUTIVE"
    TABLE_NOT_STITCHED = "TABLE_NOT_STITCHED"


@dataclass
class MutationOutcome:
    """What happened, in terms the endpoint maps to HTTP.

    No ``HTTPException`` is raised in this module: keeping it out means the
    service is testable without a client and stays importable from a future
    Celery task.
    """

    changed: bool = False
    error: MutationError | None = None
    detail: str | None = None
    table_count_before: int = 0
    table_count_after: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


class _Refused(Exception):
    """Internal control flow, converted to a MutationOutcome before returning."""

    def __init__(self, error: MutationError, detail: str) -> None:
        self.error = error
        self.detail = detail


def _count_tables(session: Session, document_id: UUID) -> int:
    return (
        session.query(func.count(Table.id))
        .filter(Table.document_id == document_id)
        .scalar()
        or 0
    )


def _lock_document(session: Session, document_id: UUID) -> Document:
    """Retrieves and locks a document row for the current transaction,
    then verifies that the document is completed before allowing edits.
    """
    session.execute(lock_timeout_statement())
    document = session.get(Document, document_id, with_for_update=True)
    if document is None:
        # The access dependency already resolved it, so this means it was deleted
        # between that query and this lock.
        raise _Refused(
            MutationError.DOCUMENT_NOT_PROCESSED, "Document is no longer available"
        )
    if document.status != "completed":
        raise _Refused(
            MutationError.DOCUMENT_NOT_PROCESSED,
            f"Document is {document.status or 'not processed'}; tables cannot be edited",
        )
    return document


def _resolve_table(session: Session, document_id: UUID, table_id: UUID) -> Table:
    table = session.get(Table, table_id)
    # A table belonging to another document is reported the same way as one that
    # does not exist, so ids cannot be probed across documents.
    if table is None or table.document_id != document_id:
        raise _Refused(MutationError.TABLE_NOT_FOUND, f"No table {table_id}")
    return table


def _run(session: Session, document_id: UUID, operation) -> MutationOutcome:
    """Lock, gate, run, and record the table count on either side."""
    try:
        document = _lock_document(session, document_id)
        before = _count_tables(session, document_id)
        operation(session, document_id)
        after = _count_tables(session, document_id)
    except _Refused as refused:
        return MutationOutcome(error=refused.error, detail=refused.detail)

    changed = before != after
    if changed:
        document.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return MutationOutcome(
        changed=changed,
        table_count_before=before,
        table_count_after=after,
    )


# --- sync operations --------------------------------------------------------


def _merge_all(session: Session, document_id: UUID) -> MutationOutcome:
    return _run(session, document_id, merge_consecutive_tables)


def _split_all(session: Session, document_id: UUID) -> MutationOutcome:
    return _run(session, document_id, split_consecutive_tables)


def _merge_pair(
    session: Session, document_id: UUID, previous_id: UUID, next_id: UUID
) -> MutationOutcome:
    def operation(session: Session, document_id: UUID) -> None:
        previous = _resolve_table(session, document_id, previous_id)
        following = _resolve_table(session, document_id, next_id)

        previous_parts = ordered_parts(session, previous)
        following_parts = ordered_parts(session, following)
        if not previous_parts or not following_parts:
            raise _Refused(
                MutationError.TABLES_NOT_CONSECUTIVE,
                "One of the tables has no content on any page",
            )

        boundary_prev, boundary_next = previous_parts[-1], following_parts[0]
        if not is_consecutive(session, boundary_prev, boundary_next):
            raise _Refused(
                MutationError.TABLES_NOT_CONSECUTIVE,
                _explain_not_consecutive(session, boundary_prev, boundary_next),
            )
        merge_two_tables(session, previous, following)

    return _run(session, document_id, operation)


def _split_one(session: Session, document_id: UUID, table_id: UUID) -> MutationOutcome:
    def operation(session: Session, document_id: UUID) -> None:
        table = _resolve_table(session, document_id, table_id)
        if len(ordered_parts(session, table)) <= 1:
            raise _Refused(
                MutationError.TABLE_NOT_STITCHED,
                "This table sits on a single page, so there is nothing to split",
            )
        split_table(session, table)

    return _run(session, document_id, operation)


def _explain_not_consecutive(
    session: Session, previous: RegionTable, following: RegionTable
) -> str:
    """Say which rule failed.

    The page-extremity rule is surprising -- a footer after the table defeats it
    -- and a bare "not consecutive" generates bug reports.
    """
    previous_cols = (previous.col_end or 0) - (previous.col_start or 0) + 1
    following_cols = (following.col_end or 0) - (following.col_start or 0) + 1
    if previous_cols != following_cols:
        return (
            f"The tables have different column counts "
            f"({previous_cols} and {following_cols})"
        )

    previous_page = previous.region.page if previous.region else None
    following_page = following.region.page if following.region else None
    if previous_page is None or following_page is None:
        return "One of the tables is not attached to a page"

    if (previous_page.page_number or 0) + 1 != (following_page.page_number or 0):
        return (
            f"The tables are not on adjacent pages "
            f"(pages {previous_page.page_number} and {following_page.page_number})"
        )

    last_on_page = (
        session.query(func.max(Region.reading_order))
        .filter(Region.page_id == previous_page.id)
        .scalar()
    )
    if previous.region.reading_order != last_on_page:
        return (
            f"The first table does not end at the bottom of page "
            f"{previous_page.page_number}; something else follows it"
        )
    return (
        f"The second table does not start at the top of page "
        f"{following_page.page_number}; something else precedes it"
    )


def _merge_candidates(
    session: Session, document_id: UUID
) -> list[tuple[UUID, UUID, int]]:
    """Every adjacent table pair that :func:`is_consecutive` accepts.

    Computed with the same predicate the merge endpoint enforces, so a button the
    UI enables is guaranteed to merge rather than 409.
    """
    tables = session.query(Table).filter(Table.document_id == document_id).all()

    ordered: list[tuple[int, int, Table, list[RegionTable]]] = []
    for table in tables:
        parts = ordered_parts(session, table)
        if not parts:
            continue
        first = parts[0]
        page = first.region.page if first.region else None
        if page is None:
            continue
        ordered.append(
            (page.page_number or 0, first.region.reading_order or 0, table, parts)
        )
    ordered.sort(key=lambda item: (item[0], item[1]))

    candidates: list[tuple[UUID, UUID, int]] = []
    for (_, _, previous, previous_parts), (
        _,
        _,
        following,
        following_parts,
    ) in pairwise(ordered):
        boundary_prev = previous_parts[-1]
        boundary_next = following_parts[0]
        if is_consecutive(session, boundary_prev, boundary_next):
            boundary_page = boundary_prev.region.page.page_number
            candidates.append((previous.id, following.id, boundary_page))
    return candidates


# --- async wrappers ---------------------------------------------------------


async def _apply(db: AsyncSession, operation) -> MutationOutcome:
    """Run one sync operation, commit it, and turn a lock timeout into an outcome."""
    try:
        outcome = await db.run_sync(operation)
    except DBAPIError as exc:
        if is_lock_timeout(exc):
            await db.rollback()
            return MutationOutcome(
                error=MutationError.DOCUMENT_BUSY,
                detail="The document is being processed; try again shortly",
            )
        raise

    if outcome.ok:
        # Committed out here, never inside run_sync: the sync session shares this
        # transaction, and committing in there would end it under the caller.
        await db.commit()
    else:
        await db.rollback()
    return outcome


async def merge_all_consecutive(db: AsyncSession, document_id: UUID) -> MutationOutcome:
    return await _apply(db, lambda session: _merge_all(session, document_id))


async def split_all_consecutive(db: AsyncSession, document_id: UUID) -> MutationOutcome:
    return await _apply(db, lambda session: _split_all(session, document_id))


async def merge_table_pair(
    db: AsyncSession, document_id: UUID, previous_id: UUID, next_id: UUID
) -> MutationOutcome:
    return await _apply(
        db, lambda session: _merge_pair(session, document_id, previous_id, next_id)
    )


async def split_one_table(
    db: AsyncSession, document_id: UUID, table_id: UUID
) -> MutationOutcome:
    return await _apply(db, lambda session: _split_one(session, document_id, table_id))


async def list_merge_candidates(
    db: AsyncSession, document_id: UUID
) -> list[tuple[UUID, UUID, int]]:
    return await db.run_sync(lambda session: _merge_candidates(session, document_id))


async def document_updated_at(db: AsyncSession, document_id: UUID) -> datetime | None:
    result = await db.execute(
        select(Document.updated_at).where(Document.id == document_id)
    )
    return result.scalar_one_or_none()


__all__ = [
    "MutationError",
    "MutationOutcome",
    "document_updated_at",
    "list_merge_candidates",
    "merge_all_consecutive",
    "merge_table_pair",
    "split_all_consecutive",
    "split_one_table",
]
