from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Page, Region, RegionText, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.search import SearchHit, SearchSnippet

router = APIRouter()

_SNIPPET_RADIUS = 60
_MAX_SNIPPETS = 5


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


@router.get("/", response_model=list[SearchHit])
async def search_documents(
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    term = q.strip()
    if not term:
        return []

    result = await db.execute(
        select(Document, Page, Region, RegionText)
        .join(Page, Page.document_id == Document.id)
        .join(Region, Region.page_id == Page.id)
        .join(RegionText, RegionText.region_id == Region.id)
        .where(
            Document.owner_id == current_user.id,
            RegionText.text_content.ilike(_like(term), escape="\\"),
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
