import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps import DocumentAccess, require_edit_access, require_read_access
from app.schemas.table import (
    MergeCandidate,
    MergeCandidatesOut,
    MergeTablesRequest,
    TableMutationResult,
    TableNode,
)
from app.services.document_tree import load_tables
from app.services.table_edit import (
    MutationError,
    MutationOutcome,
    document_updated_at,
    list_merge_candidates,
    merge_all_consecutive,
    merge_table_pair,
    split_all_consecutive,
    split_one_table,
)

_STATUS_BY_ERROR = {
    MutationError.DOCUMENT_NOT_PROCESSED: status.HTTP_409_CONFLICT,
    MutationError.DOCUMENT_BUSY: status.HTTP_409_CONFLICT,
    MutationError.TABLES_NOT_CONSECUTIVE: status.HTTP_409_CONFLICT,
    MutationError.TABLE_NOT_STITCHED: status.HTTP_409_CONFLICT,
    # Consistent with the document rule: something that is not in this document
    # is reported as absent rather than forbidden.
    MutationError.TABLE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
}

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{document_id}/tables", response_model=list[TableNode])
async def list_document_tables(
    include_cells: bool = Query(
        default=False,
        description="Cells are omitted by default; the panel only needs shapes.",
    ),
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Every logical table in the document, in document order."""
    return await load_tables(db, access, include_cells=include_cells)


@router.get("/{document_id}/tables/merge-candidates", response_model=MergeCandidatesOut)
async def get_merge_candidates(
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Get the table pairs that may be merged and those that may be split"""
    
    candidates = await list_merge_candidates(db, access.document.id)
    tables = await load_tables(db, access, include_cells=False)
    return MergeCandidatesOut(
        candidates=[
            MergeCandidate(
                previous_table_id=previous,
                next_table_id=following,
                boundary_page=boundary_page,
            )
            for previous, following, boundary_page in candidates
        ],
        splittable_table_ids=[table.id for table in tables if len(table.parts) > 1],
        can_edit=access.can_edit,
    )


async def _mutation_result(
    db: AsyncSession, access: DocumentAccess, outcome: MutationOutcome
) -> TableMutationResult:
    """Map a refusal to HTTP, or build the post-edit payload"""
    if not outcome.ok:
        raise HTTPException(
            status_code=_STATUS_BY_ERROR[outcome.error],
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    return TableMutationResult(
        changed=outcome.changed,
        table_count_before=outcome.table_count_before,
        table_count_after=outcome.table_count_after,
        tables=await load_tables(db, access, include_cells=False),
        document_updated_at=await document_updated_at(db, access.document.id),
    )


@router.post(
    "/{document_id}/tables/merge-consecutive", response_model=TableMutationResult
)
async def merge_consecutive(
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Join every table that continues onto the next page"""
    outcome = await merge_all_consecutive(db, access.document.id)
    return await _mutation_result(db, access, outcome)


@router.post(
    "/{document_id}/tables/split-consecutive", response_model=TableMutationResult
)
async def split_consecutive(
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Break every stitched table back into one table per page"""
    outcome = await split_all_consecutive(db, access.document.id)
    return await _mutation_result(db, access, outcome)


@router.post("/{document_id}/tables/merge", response_model=TableMutationResult)
async def merge_two(
    payload: MergeTablesRequest,
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Merge one specific consecutive pair the user selected"""
    outcome = await merge_table_pair(
        db, access.document.id, payload.previous_table_id, payload.next_table_id
    )
    return await _mutation_result(db, access, outcome)


@router.post(
    "/{document_id}/tables/{table_id}/split", response_model=TableMutationResult
)
async def split_one(
    table_id: UUID,
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Split a single stitched table, so one merge can be undone on its own."""
    outcome = await split_one_table(db, access.document.id, table_id)
    return await _mutation_result(db, access, outcome)
