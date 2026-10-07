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

router = APIRouter()

_SNIPPET_RADIUS = 60
_MAX_SNIPPETS = 5
_ALLOWED_FILE_TYPES = frozenset(CONTENT_TYPE_BY_EXTENSION.values())


def _like(term: str) -> str:
    # treat % and _ as normal characters
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, term: str) -> tuple[str, int, int] | None:
    # first match, with a short window of surrounding text
    idx = text.lower().find(term.lower())
    if idx < 0:
        return None
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(term) + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    snippet = prefix + text[start:end] + suffix
    match_start = len(prefix) + (idx - start)
    match_end = match_start + len(term)
    return snippet, match_start, match_end


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
    clauses = _filter_clauses(date_from, date_to, file_type)
    if not term and not clauses:
        return []

    if term:
        result = await db.execute(
            select(Document, Page, Region, RegionText)
            .join(Page, Page.document_id == Document.id)
            .join(Region, Region.page_id == Page.id)
            .join(RegionText, RegionText.region_id == Region.id)
            .where(
                Document.owner_id == current_user.id,
                RegionText.text_content.ilike(_like(term), escape="\\"),
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
            built = _snippet(region_text.text_content or "", term)
            if built is None:
                continue
            snippet_text, match_start, match_end = built
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
                    match_start=match_start,
                    match_end=match_end,
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
