"""Correct and revert extracted text at the region level """

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps import DocumentAccess, require_completed, require_edit_access
from app.schemas.corrections import RegionTextEdit, RegionTextMutationResult
from app.services.corrections import (
    CorrectionError,
    CorrectionOutcome,
    undo_region_correction,
    update_region_text,
)
from app.services.table_edit import document_updated_at

_STATUS_BY_ERROR = {
    CorrectionError.DOCUMENT_NOT_PROCESSED: status.HTTP_409_CONFLICT,
    CorrectionError.DOCUMENT_BUSY: status.HTTP_409_CONFLICT,
    CorrectionError.NOT_CORRECTED: status.HTTP_409_CONFLICT,
    CorrectionError.REGION_NOT_EDITABLE: status.HTTP_409_CONFLICT,
    # Consistent with the document rule: something that is not in this document
    # is reported as absent rather than forbidden.
    CorrectionError.REGION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
}

router = APIRouter()
logger = logging.getLogger(__name__)


async def _mutation_result(
    db: AsyncSession, document_id: UUID, outcome: CorrectionOutcome
) -> RegionTextMutationResult:
    """Map a refusal to HTTP, or build the post-edit payload"""
    if not outcome.ok:
        logger.info("Correction refused: %s (%s)", outcome.error.value, outcome.detail)
        raise HTTPException(
            status_code=_STATUS_BY_ERROR[outcome.error],
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    return RegionTextMutationResult(
        changed=outcome.changed,
        document_updated_at=await document_updated_at(db, document_id),
    )


@router.put(
    "/{document_id}/regions/{region_id}/text",
    response_model=RegionTextMutationResult,
)
async def correct_region_text(
    document_id: UUID,
    region_id: UUID,
    payload: RegionTextEdit,
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Replace one region's extracted text; the raw OCR output is preserved"""
    require_completed(access)
    # Capture the id before the call: a refused outcome rolls the session
    # back, which expires the document row the dependency loaded.
    id_ = access.document.id
    outcome = await update_region_text(db, id_, region_id, payload.text)
    return await _mutation_result(db, id_, outcome)


@router.delete(
    "/{document_id}/regions/{region_id}/correction",
    response_model=RegionTextMutationResult,
)
async def revert_region_text(
    document_id: UUID,
    region_id: UUID,
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Restore one region's text to the raw OCR output"""
    require_completed(access)
    id_ = access.document.id
    outcome = await undo_region_correction(db, id_, region_id)
    return await _mutation_result(db, id_, outcome)
