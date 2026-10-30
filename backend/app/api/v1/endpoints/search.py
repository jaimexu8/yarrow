from datetime import date, datetime, time, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Page, Region, RegionText, User

from app.api.v1.endpoints.documents import CONTENT_TYPE_BY_EXTENSION
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.search import SearchHit, SearchSnippet
from app.services.search_query import first_match_span, parse_query, terms_match

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


@router.get("/", response_model=list[SearchHit])
async def search_documents(
    q: str = Query(default=""),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    file_type: str | None = Query(default=None),
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
        result = await db.execute(
            select(Document, Page, Region, RegionText)
            .join(Page, Page.document_id == Document.id)
            .join(Region, Region.page_id == Page.id)
            .join(RegionText, RegionText.region_id == Region.id)
            .where(
                Document.owner_id == current_user.id,
                *clauses,
            )
            .order_by(
                Document.created_at.desc(),
                Page.page_number,
                nulls_last(Region.reading_order),
            )
        )

        hits: dict[UUID, SearchHit] = {}
        for document, page, region, region_text in result.all():
            text = region_text.text_content or ""
            if not terms_match(text, terms):
                continue
            span = first_match_span(text, terms[0])
            if span is None:
                continue
            match_start, match_end = span
            start = max(0, match_start - _SNIPPET_RADIUS)
            end = min(len(text), match_end + _SNIPPET_RADIUS)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            snippet_text = prefix + text[start:end] + suffix
            # match_start/match_end are offsets into the snippet text, so the
            # client can highlight the exact characters that matched (which for
            # an OCR confusable or typo differ from the query, e.g. "l.1").
            snippet_match_start = len(prefix) + (match_start - start)
            snippet_match_end = snippet_match_start + (match_end - match_start)

            hit = hits.get(document.id)
            if hit is None:
                hit = SearchHit(id=document.id, filename=document.filename, snippets=[])
                hits[document.id] = hit
            if len(hit.snippets) >= _MAX_SNIPPETS:
                continue
            hit.snippets.append(
                SearchSnippet(
                    region_id=region.id,
                    page_number=page.page_number,
                    text=snippet_text,
                    match_start=snippet_match_start,
                    match_end=snippet_match_end,
                    edited=region_text.original_text is not None,
                )
            )

        return list(hits.values())

    # Browse mode: no term, so no snippets and just the documents that match
    # the filters.
    result = await db.execute(
        select(Document)
        .where(Document.owner_id == current_user.id, *clauses)
        .order_by(Document.created_at.desc())
    )
    return [
        SearchHit(id=document.id, filename=document.filename, snippets=[])
        for document in result.scalars().all()
    ]
