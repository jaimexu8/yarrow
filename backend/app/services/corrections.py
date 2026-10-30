"""Correct and revert extracted text (us-51, us-100).

Same shape as :mod:`app.services.table_edit`: the sync logic returns a
pathname-free outcome object (no ``HTTPException`` here) after locking the
document row, and the endpoint maps the outcome to HTTP and commits.

Storage model: ``RegionText.text_content`` always holds the active text. It
starts as the raw OCR output and becomes the correction after the first edit;
the untouched OCR output is preserved verbatim in ``RegionText.original_text``.
``original_text IS NOT NULL`` therefore means "has a correction". Undo restores
``text_content`` from ``original_text`` and nulls it.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import Document, Region, RegionText
from yarrow_db.models.region import RegionType

logger = logging.getLogger(__name__)


class CorrectionError(str, Enum):
    DOCUMENT_NOT_PROCESSED = "DOCUMENT_NOT_PROCESSED"
    DOCUMENT_BUSY = "DOCUMENT_BUSY"
    REGION_NOT_FOUND = "REGION_NOT_FOUND"
    REGION_NOT_EDITABLE = "REGION_NOT_EDITABLE"
    NOT_CORRECTED = "NOT_CORRECTED"


@dataclass
class CorrectionOutcome:
    """What happened, in terms the endpoint maps to HTTP.

    No ``HTTPException`` is raised in this module, so this stays testable
    without a client.
    """

    changed: bool = False
    error: CorrectionError | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class _Refused(Exception):
    """Internal control flow, converted to a CorrectionOutcome before returning."""

    def __init__(self, error: CorrectionError, detail: str) -> None:
        self.error = error
        self.detail = detail


def _lock_document(session: Session, document_id: UUID) -> Document:
    """Retrieve and lock a document row, verifying it has finished processing."""
    session.execute(lock_timeout_statement())
    document = session.get(Document, document_id, with_for_update=True)
    if document is None:
        # The access dependency already resolved it, so this means it was
        # deleted between that query and this lock.
        raise _Refused(
            CorrectionError.DOCUMENT_NOT_PROCESSED, "Document is no longer available"
        )
    if document.status != "completed":
        raise _Refused(
            CorrectionError.DOCUMENT_NOT_PROCESSED,
            f"Document is {document.status or 'not processed'}; text cannot be corrected",
        )
    return document


def _resolve_region_text(
    session: Session, document_id: UUID, region_id: UUID
) -> RegionText:
    """The text row of one editable region of this document.

    Table regions are refused: their text lives in ``table_cells``.
    The "no text row" case and the cross-document case are both
    refused so region ids cannot be probed across documents.
    """
    region = session.get(Region, region_id)
    page = region.page if region is not None else None
    if region is None or page is None or page.document_id != document_id:
        raise _Refused(CorrectionError.REGION_NOT_FOUND, f"No region {region_id}")

    text_row = (
        session.execute(select(RegionText).where(RegionText.region_id == region_id))
    ).scalar_one_or_none()

    if region.region_type == RegionType.TABLE:
        raise _Refused(
            CorrectionError.REGION_NOT_EDITABLE,
            "Table text is corrected in its cells, not as one block",
        )
    if text_row is None:
        raise _Refused(
            CorrectionError.REGION_NOT_EDITABLE, "This region has no extracted text"
        )
    return text_row


def _update_region_text(
    session: Session, document_id: UUID, region_id: UUID, new_text: str
) -> CorrectionOutcome:
    """Store one correction; keep the raw OCR output if it is not stored yet."""
    try:
        document = _lock_document(session, document_id)
        text_row = _resolve_region_text(session, document_id, region_id)
    except _Refused as refused:
        return CorrectionOutcome(error=refused.error, detail=refused.detail)

    changed = (text_row.text_content or "") != new_text
    if changed:
        if text_row.original_text is None:
            text_row.original_text = text_row.text_content
        text_row.text_content = new_text
        document.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return CorrectionOutcome(changed=changed)


def _undo_region_correction(
    session: Session, document_id: UUID, region_id: UUID
) -> CorrectionOutcome:
    """Restore the active text from the preserved OCR output."""
    try:
        document = _lock_document(session, document_id)
        text_row = _resolve_region_text(session, document_id, region_id)
        if text_row.original_text is None:
            raise _Refused(
                CorrectionError.NOT_CORRECTED,
                "This region has no correction to revert",
            )
    except _Refused as refused:
        return CorrectionOutcome(error=refused.error, detail=refused.detail)

    text_row.text_content = text_row.original_text
    text_row.original_text = None
    document.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return CorrectionOutcome(changed=True)


# --- async wrappers ---------------------------------------------------------


async def _apply(db: AsyncSession, operation) -> CorrectionOutcome:
    """Run one sync operation, commit it, and turn a lock timeout into an outcome."""
    try:
        outcome = await db.run_sync(operation)
    except DBAPIError as exc:
        if is_lock_timeout(exc):
            await db.rollback()
            return CorrectionOutcome(
                error=CorrectionError.DOCUMENT_BUSY,
                detail="The document is being processed; try again shortly",
            )
        raise

    if outcome.ok:
        # Committed out here, never inside run_sync: the sync session shares
        # this transaction, and committing in there would end it under the caller.
        await db.commit()
    else:
        await db.rollback()
    return outcome


async def update_region_text(
    db: AsyncSession, document_id: UUID, region_id: UUID, new_text: str
) -> CorrectionOutcome:
    return await _apply(
        db, lambda session: _update_region_text(session, document_id, region_id, new_text)
    )


async def undo_region_correction(
    db: AsyncSession, document_id: UUID, region_id: UUID
) -> CorrectionOutcome:
    return await _apply(
        db, lambda session: _undo_region_correction(session, document_id, region_id)
    )


__all__ = [
    "CorrectionError",
    "CorrectionOutcome",
    "undo_region_correction",
    "update_region_text",
]
