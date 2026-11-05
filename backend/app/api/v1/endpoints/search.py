from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import (
    Document,
    Page,
    Region,
    RegionTable,
    RegionText,
    TableCell,
    User,
)

from app.api.v1.endpoints.documents import CONTENT_TYPE_BY_EXTENSION
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.search import SearchHit, SearchSnippet, TableCellMatch
from app.services.search_query import (
    Term,
    first_match_span,
    parse_query,
    terms_match,
)

router = APIRouter()

_SNIPPET_RADIUS = 60
_MAX_SNIPPETS = 5
# Queries longer than this would generate a very large regex and slow the
# index scan down; real searches are much shorter (NFR-12: clear error).
_MAX_QUERY_LENGTH = 500
_ALLOWED_FILE_TYPES = frozenset(CONTENT_TYPE_BY_EXTENSION.values())


def _validate_filters(
    date_from: date | None, date_to: date | None, file_type: str | None
) -> None:
    if file_type is not None and file_type not in _ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file_type '{file_type}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_FILE_TYPES))}",
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422, detail="date_from must be on or before date_to"
        )


def _filter_clauses(
    date_from: date | None, date_to: date | None, file_type: str | None
) -> list:
    # Dates are UTC calendar days: date_to includes the whole day.
    clauses = []
    if date_from is not None:
        clauses.append(Document.created_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        clauses.append(
            Document.created_at
            < datetime.combine(date_to + timedelta(days=1), time.min)
        )
    if file_type is not None:
        clauses.append(Document.file_type == file_type)
    return clauses


def _document_order(sort: Literal["date", "name"]):
    # date: newest first. name: a-z, case-insensitive.
    if sort == "name":
        return (nulls_last(func.lower(Document.filename)), Document.id)
    return (nulls_last(Document.created_at.desc()), Document.id)


def _snippet(text: str, terms: list[Term]) -> tuple[str, int, int] | None:
    if not terms_match(text, terms):
        return None
    span = first_match_span(text, terms[0])
    if span is None:
        return None
    match_start, match_end = span
    start = max(0, match_start - _SNIPPET_RADIUS)
    end = min(len(text), match_end + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    # match_start/match_end are offsets into the snippet text, so the
    # client can highlight the exact characters that matched (which for
    # an OCR confusable or typo differ from the query, e.g. "l.1").
    snippet_start = len(prefix) + (match_start - start)
    return (
        prefix + text[start:end] + suffix,
        snippet_start,
        snippet_start + (match_end - match_start),
    )


def _position_key(page: Page, region: Region, row: int, col: int) -> tuple:
    reading_order = region.reading_order
    return (
        page.page_number,
        reading_order is None,
        reading_order if reading_order is not None else 0,
        row,
        col,
    )


async def _collect_text_snippets(
    db: AsyncSession,
    current_user: User,
    clauses: list,
    terms: list[Term],
    candidates: dict[UUID, list[tuple[tuple, SearchSnippet]]],
    documents: dict[UUID, Document],
) -> None:
    result = await db.execute(
        select(Document, Page, Region, RegionText)
        .join(Page, Page.document_id == Document.id)
        .join(Region, Region.page_id == Page.id)
        .join(RegionText, RegionText.region_id == Region.id)
        .where(
            Document.owner_id == current_user.id,
            Document.deleted_at.is_(None),
            *clauses,
        )
        .order_by(Page.page_number, nulls_last(Region.reading_order))
    )
    for document, page, region, region_text in result.all():
        snippet = _snippet(region_text.text_content or "", terms)
        if snippet is None:
            continue
        text, match_start, match_end = snippet
        documents[document.id] = document
        candidates.setdefault(document.id, []).append(
            (
                _position_key(page, region, 0, 0),
                SearchSnippet(
                    region_id=region.id,
                    page_number=page.page_number,
                    text=text,
                    match_start=match_start,
                    match_end=match_end,
                    edited=region_text.original_text is not None,
                ),
            )
        )


async def _collect_cell_snippets(
    db: AsyncSession,
    current_user: User,
    clauses: list,
    terms: list[Term],
    candidates: dict[UUID, list[tuple[tuple, SearchSnippet]]],
    documents: dict[UUID, Document],
) -> None:
    result = await db.execute(
        select(Document, Page, Region, RegionTable, TableCell)
        .join(Page, Page.document_id == Document.id)
        .join(Region, Region.page_id == Page.id)
        .join(RegionTable, RegionTable.region_id == Region.id)
        .join(TableCell, TableCell.region_table_id == RegionTable.id)
        .where(
            Document.owner_id == current_user.id,
            Document.deleted_at.is_(None),
            TableCell.text_content.is_not(None),
            *clauses,
        )
        .order_by(
            Page.page_number,
            nulls_last(Region.reading_order),
            RegionTable.row_start + TableCell.row_idx,
            RegionTable.col_start + TableCell.col_idx,
        )
    )
    matches = []
    for document, page, region, region_table, cell in result.all():
        snippet = _snippet(cell.text_content, terms)
        if snippet is not None:
            matches.append((document, page, region, region_table, cell, snippet))
    if not matches:
        return

    headers = await _header_cells(
        db, {region_table.table_id for _d, _p, _r, region_table, _c, _s in matches}
    )
    for document, page, region, region_table, cell, snippet in matches:
        text, match_start, match_end = snippet
        row = (region_table.row_start or 0) + (cell.row_idx or 0)
        col = (region_table.col_start or 0) + (cell.col_idx or 0)
        is_header = bool(cell.is_header)
        documents[document.id] = document
        candidates.setdefault(document.id, []).append(
            (
                _position_key(page, region, row, col),
                SearchSnippet(
                    region_id=region.id,
                    page_number=page.page_number,
                    text=text,
                    match_start=match_start,
                    match_end=match_end,
                    edited=False,
                    source="table_cell",
                    table_cell=TableCellMatch(
                        table_id=region_table.table_id,
                        region_table_id=region_table.id,
                        cell_id=cell.id,
                        row=row,
                        col=col,
                        row_span=cell.row_span or 1,
                        col_span=cell.col_span or 1,
                        is_header=is_header,
                        column_header=None
                        if is_header
                        else _column_header(headers.get(region_table.table_id, []), col),
                    ),
                ),
            )
        )


async def _header_cells(
    db: AsyncSession, table_ids: set[UUID]
) -> dict[UUID, list[tuple[int, int, int, str | None]]]:
    result = await db.execute(
        select(RegionTable, TableCell)
        .join(TableCell, TableCell.region_table_id == RegionTable.id)
        .where(RegionTable.table_id.in_(table_ids), TableCell.is_header.is_(True))
    )
    headers: dict[UUID, list[tuple[int, int, int, str | None]]] = {}
    for region_table, cell in result.all():
        row = (region_table.row_start or 0) + (cell.row_idx or 0)
        col = (region_table.col_start or 0) + (cell.col_idx or 0)
        headers.setdefault(region_table.table_id, []).append(
            (row, col, col + (cell.col_span or 1) - 1, cell.text_content)
        )
    return headers


def _column_header(
    headers: list[tuple[int, int, int, str | None]], col: int
) -> str | None:
    covering = [header for header in headers if header[1] <= col <= header[2]]
    if not covering:
        return None
    text = min(covering, key=lambda header: (header[0], header[1]))[3]
    if text is None or not text.strip():
        return None
    return text.strip()


@router.get("/", response_model=list[SearchHit])
async def search_documents(
    q: str = Query(default=""),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    file_type: str | None = Query(default=None),
    sort: Literal["date", "name"] = Query(default="date"),
    scope: Literal["all", "text", "tables"] = Query(default="all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _validate_filters(date_from, date_to, file_type)

    term = q.strip()
    if len(term) > _MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Search query is too long ({len(term)} characters). "
                f"Keep it under {_MAX_QUERY_LENGTH}."
            ),
        )

    clauses = _filter_clauses(date_from, date_to, file_type)
    if not term and not clauses:
        return []

    if term:
        terms = parse_query(term)
        if not terms:
            return []
        candidates: dict[UUID, list[tuple[tuple, SearchSnippet]]] = {}
        documents: dict[UUID, Document] = {}
        if scope in ("all", "text"):
            await _collect_text_snippets(
                db, current_user, clauses, terms, candidates, documents
            )
        if scope in ("all", "tables"):
            await _collect_cell_snippets(
                db, current_user, clauses, terms, candidates, documents
            )
        if not candidates:
            return []

        ordered = await db.execute(
            select(Document.id)
            .where(Document.id.in_(list(candidates)))
            .order_by(*_document_order(sort))
        )
        hits = []
        for document_id in ordered.scalars().all():
            document = documents[document_id]
            snippets = [
                snippet
                for _key, snippet in sorted(
                    candidates[document_id], key=lambda item: item[0]
                )
            ]
            hits.append(
                SearchHit(
                    id=document.id,
                    filename=document.filename,
                    snippets=snippets[:_MAX_SNIPPETS],
                )
            )
        return hits

    # Browse mode: no term, so no snippets and just the documents that match
    # the filters.
    result = await db.execute(
        select(Document)
        .where(
            Document.owner_id == current_user.id,
            Document.deleted_at.is_(None),
            *clauses,
        )
        .order_by(*_document_order(sort))
    )
    return [
        SearchHit(id=document.id, filename=document.filename, snippets=[])
        for document in result.scalars().all()
    ]
